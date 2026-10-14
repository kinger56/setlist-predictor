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

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from app.backtest import backtest
from app.cache import Cache
from app.predictor import ROTATING, LIKELY, SongStats, predict
from app.setlistfm import SetlistFmClient, SetlistFmError

load_dotenv()  # reads .env into environment variables (local development only)

STATIC_DIR = Path(__file__).parent / "static"
# MusicBrainz IDs are UUIDs: 36 characters of hex digits and dashes.
MBID_PATTERN = r"^[0-9a-fA-F-]{36}$"

app = FastAPI(title="Setlist Predictor")


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
    }


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
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
