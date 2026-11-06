"""The web API, built with FastAPI.

Routes:
  GET /                 -> the web page (app/static/index.html)
  GET /api/health       -> {"status": "ok"}, handy for hosting health checks
  GET /api/artists?q=   -> artists matching a name
  GET /api/predict?mbid -> predicted setlist for an artist
  GET /api/backtest?mbid-> accuracy of predictions on the artist's past shows

This file stays thin on purpose: it handles HTTP (parsing parameters,
choosing status codes, shaping JSON) and hands the real work to
setlistfm.py, predictor.py and backtest.py.

Run locally:  uvicorn app.main:app --reload
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.backtest import backtest
from app.cache import Cache
from app.client_ip import client_ip
from app.predictor import ROTATING, LIKELY, SongStats, predict
from app.setlistfm import SetlistFmClient, SetlistFmError
from app.throttle import Throttle

load_dotenv()  # reads .env into environment variables (local development only)

STATIC_DIR = Path(__file__).parent / "static"
# MusicBrainz IDs are UUIDs: 36 characters of hex digits and dashes.
MBID_PATTERN = r"^[0-9a-fA-F-]{36}$"

app = FastAPI(title="Setlist Predictor")

# Each visitor gets this many API requests per window. A predict or backtest
# can call setlist.fm several times, and the key's daily quota is shared by
# everyone, so one visitor (or bot) shouldn't be able to use it all up.
# (Generous, because campus Wi-Fi puts many people behind one public IP.)
API_REQUESTS_PER_WINDOW = 120
API_WINDOW_SECONDS = 5 * 60
app.state.api_throttle = Throttle(API_REQUESTS_PER_WINDOW, API_WINDOW_SECONDS)


def _inline_hashes(html: str, tag: str) -> list[str]:
    """CSP hashes for the page's inline <script>/<style> blocks, so the
    browser runs exactly those and refuses any other inline code."""
    blocks = re.findall(rf"<{tag}>(.*?)</{tag}>", html, flags=re.DOTALL)
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(b.encode()).digest()).decode() + "'"
        for b in blocks
    ]


_PAGE = (STATIC_DIR / "index.html").read_text()
CSP = "; ".join([
    "default-src 'self'",
    "script-src " + " ".join(_inline_hashes(_PAGE, "script")),
    "style-src " + " ".join(_inline_hashes(_PAGE, "style")),
    "img-src 'self' data:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",  # can't be shown inside another site (clickjacking)
])


@app.middleware("http")
async def protect(request: Request, call_next):
    """Rate-limit the API per visitor, and add security headers."""
    path = request.url.path
    if path.startswith("/api/") and path != "/api/health":
        ip = client_ip(request)
        wait = request.app.state.api_throttle.retry_after(ip)
        if wait:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Too many requests. Try again in {wait} seconds."},
                headers={"Retry-After": str(wait)},
            )
        request.app.state.api_throttle.record(ip)

    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if not path.startswith(("/docs", "/redoc")):  # Swagger loads code from a CDN
        headers.setdefault("Content-Security-Policy", CSP)
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


@lru_cache  # build the client once and reuse it, so the cache and rate limiter are shared
def _build_client(api_key: str, cache_path: str) -> SetlistFmClient:
    return SetlistFmClient(api_key, cache=Cache(cache_path))


def get_client() -> SetlistFmClient:
    """FastAPI calls this to give each route a client. Tests replace it with a
    fake using app.dependency_overrides, so they never touch the network."""
    api_key = os.getenv("SETLISTFM_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Server is missing SETLISTFM_API_KEY. See README for setup.",
        )
    return _build_client(api_key, os.getenv("CACHE_DB", "cache.db"))


@app.exception_handler(SetlistFmError)
def setlistfm_error_handler(request: Request, exc: SetlistFmError) -> JSONResponse:
    # 502 Bad Gateway = "a server we depend on failed", which is exactly this.
    return JSONResponse(status_code=502, content={"detail": str(exc)})


def _song_json(song: SongStats) -> dict:
    return {
        "name": song.name,
        "probability": round(song.probability, 3),
        "times_played": song.times_played,
        "category": song.category,
        "encore_rate": round(song.encore_rate, 2),
        "is_cover": song.is_cover,
        "last_played": song.last_played.isoformat(),
        "shows_since": song.shows_since,
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/artists")
def search_artists(
    q: str = Query(min_length=1, max_length=100),
    client: SetlistFmClient = Depends(get_client),
) -> list[dict]:
    return [
        {"mbid": a.mbid, "name": a.name, "disambiguation": a.disambiguation}
        for a in client.search_artists(q.strip())
    ]


@app.get("/api/predict")
def predict_setlist(
    mbid: str = Query(pattern=MBID_PATTERN),
    shows: int = Query(default=20, ge=5, le=60),
    client: SetlistFmClient = Depends(get_client),
) -> dict:
    recent = client.get_recent_shows(mbid, max_shows=shows)
    if not recent:
        raise HTTPException(404, "No setlists found for this artist yet.")

    prediction = predict(recent)
    predicted_names = {s.name for s in prediction.setlist}
    latest = recent[0]  # get_recent_shows returns newest first

    return {
        "shows_used": prediction.shows_used,
        "expected_length": prediction.expected_length,
        "date_range": {"from": recent[-1].date.isoformat(), "to": latest.date.isoformat()},
        "latest_show": {
            "date": latest.date.isoformat(),
            "venue": latest.venue,
            "city": latest.city,
            "tour": latest.tour,
        },
        "setlist": [_song_json(s) for s in prediction.setlist],
        # Songs that didn't make the cut but have a real chance tonight.
        "also_possible": [
            _song_json(s)
            for s in prediction.all_songs
            if s.name not in predicted_names and s.category in (LIKELY, ROTATING)
        ][:10],
    }


@app.get("/api/backtest")
def backtest_artist(
    mbid: str = Query(pattern=MBID_PATTERN),
    client: SetlistFmClient = Depends(get_client),
) -> dict:
    shows = client.get_recent_shows(mbid, max_shows=60)
    try:
        weighted = backtest(shows)
        baseline = backtest(shows, half_life=None)  # every show counts equally
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    return {
        "shows_tested": weighted.shows_tested,
        "precision": round(weighted.precision, 3),
        "recall": round(weighted.recall, 3),
        "baseline_precision": round(baseline.precision, 3),
        "baseline_recall": round(baseline.recall, 3),
    }
