"""Client for the setlist.fm REST API.

This module is the ONLY place in the project that knows about HTTP or about
setlist.fm's JSON format. It turns raw API responses into small, plain Python
objects (`Artist`, `Show`, `SongPlay`). Everything else in the app works with
those objects, which is why the prediction code can be tested without the
internet.

API docs: https://api.setlist.fm/docs/1.0/index.html
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable
from urllib.parse import urlencode

import httpx

from app.cache import Cache

BASE_URL = "https://api.setlist.fm/rest/1.0"

# Status codes worth retrying: "slow down" (429) and temporary server problems.
RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


class SetlistFmError(Exception):
    """Raised when setlist.fm can't give us an answer."""


# --------------------------------------------------------------------------
# Plain data objects
# --------------------------------------------------------------------------
# `frozen=True` makes these immutable: once a show is parsed, nothing can
# accidentally change it. That makes bugs much easier to reason about.


@dataclass(frozen=True)
class Artist:
    mbid: str  # MusicBrainz ID: setlist.fm's unique ID for an artist
    name: str
    disambiguation: str = ""  # e.g. "US rock band", to tell same-name artists apart


@dataclass(frozen=True)
class SongPlay:
    """One song, as played at one show."""

    name: str
    position: int  # 0-based position in the show (0 = opener)
    is_encore: bool
    is_cover: bool


@dataclass(frozen=True)
class Show:
    id: str
    date: date
    venue: str
    city: str
    tour: str | None
    songs: tuple[SongPlay, ...]

    @property
    def song_names(self) -> set[str]:
        return {song.name for song in self.songs}


# --------------------------------------------------------------------------
# Parsing: raw JSON -> Show
# --------------------------------------------------------------------------


def parse_setlist(raw: dict) -> Show | None:
    """Convert one setlist from the API into a `Show`.

    Returns None for setlists with no songs. setlist.fm lists upcoming shows
    and shows nobody has filled in yet, and those would drag every song's
    probability down if we counted them.
    """
    songs: list[SongPlay] = []
    position = 0

    # A setlist has one or more "sets"; encores are sets with an "encore" field.
    for song_set in (raw.get("sets") or {}).get("set") or []:
        is_encore = bool(song_set.get("encore"))
        for song in song_set.get("song") or []:
            name = (song.get("name") or "").strip()
            # "tape" songs are recordings played over the speakers (intro music),
            # not songs the band performed, so we skip them.
            if not name or song.get("tape"):
                continue
            songs.append(
                SongPlay(
                    name=name,
                    position=position,
                    is_encore=is_encore,
                    is_cover="cover" in song,
                )
            )
            position += 1

    if not songs:
        return None

    venue = raw.get("venue") or {}
    return Show(
        id=raw.get("id", ""),
        # setlist.fm dates look like "31-12-2025" (day-month-year)
        date=datetime.strptime(raw["eventDate"], "%d-%m-%Y").date(),
        venue=venue.get("name", ""),
        city=(venue.get("city") or {}).get("name", ""),
        tour=(raw.get("tour") or {}).get("name"),
        songs=tuple(songs),
    )


# --------------------------------------------------------------------------
# The HTTP client
# --------------------------------------------------------------------------


class SetlistFmClient:
    """Talks to setlist.fm politely: caches results, spaces out requests, and
    retries temporary failures with exponential backoff."""

    def __init__(
        self,
        api_key: str,
        cache: Cache | None = None,
        http: httpx.Client | None = None,
        min_interval: float = 0.6,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise SetlistFmError(
                "No API key. Set SETLISTFM_API_KEY in your .env file."
            )
        self._api_key = api_key
        self._cache = cache
        # Passing `http` in from outside ("dependency injection") lets tests
        # hand us a fake client instead of making real network calls.
        self._http = http or httpx.Client(base_url=BASE_URL, timeout=10.0)
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._sleep = sleep
        self._last_request_at = float("-inf")  # first request never waits
        self._rate_lock = threading.Lock()

    # ---- public methods ---------------------------------------------------

    def search_artists(self, name: str, limit: int = 5) -> list[Artist]:
        data = self._get(
            "/search/artists",
            {"artistName": name, "sort": "relevance", "p": 1},
        )
        if data is None:
            return []
        return [
            Artist(
                mbid=item["mbid"],
                name=item.get("name", ""),
                disambiguation=item.get("disambiguation", ""),
            )
            for item in data.get("artist", [])[:limit]
        ]

    def get_recent_shows(
        self, mbid: str, max_shows: int = 40, max_pages: int = 6
    ) -> list[Show]:
        """Most recent shows that have a real setlist, newest first."""
        shows: list[Show] = []
        page = 1
        while len(shows) < max_shows and page <= max_pages:
            data = self._get(f"/artist/{mbid}/setlists", {"p": page})
            if data is None:
                break
            for raw in data.get("setlist", []):
                show = parse_setlist(raw)
                if show is not None:
                    shows.append(show)
            # Stop when we've reached the last page of results.
            total = data.get("total", 0)
            per_page = data.get("itemsPerPage", 20) or 20
            if page * per_page >= total:
                break
            page += 1

        shows.sort(key=lambda s: s.date, reverse=True)
        return shows[:max_shows]

    # ---- internals --------------------------------------------------------

    def _get(self, path: str, params: dict) -> dict | None:
        """GET a path and return parsed JSON, or None for "no results"."""
        cache_key = f"{path}?{urlencode(sorted(params.items()))}"
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return json.loads(cached)

        headers = {"x-api-key": self._api_key, "Accept": "application/json"}

        for attempt in range(self._max_retries + 1):
            self._wait_for_turn()
            try:
                response = self._http.get(path, params=params, headers=headers)
            except httpx.TransportError:
                response = None  # network hiccup: treat it like a retryable error

            if response is not None:
                # setlist.fm answers 404 when a search finds nothing.
                if response.status_code == 404:
                    return None
                if response.status_code == 200:
                    if self._cache is not None:
                        self._cache.set(cache_key, response.text)
                    return response.json()
                if response.status_code not in RETRYABLE_STATUSES:
                    raise SetlistFmError(
                        f"setlist.fm returned HTTP {response.status_code}"
                    )

            if attempt < self._max_retries:
                # Exponential backoff: wait 1s, 2s, 4s... so we don't hammer
                # a server that's already struggling.
                self._sleep(2**attempt)

        raise SetlistFmError("setlist.fm is unavailable right now. Try again soon.")

    def _wait_for_turn(self) -> None:
        """Space requests at least `min_interval` seconds apart (rate limiting).

        The web server handles requests on several threads. Each thread
        reserves the next free time slot while holding the lock, then sleeps
        until its slot outside the lock, so two threads can never claim the
        same slot and burst past setlist.fm's limit.
        """
        with self._rate_lock:
            now = time.monotonic()
            slot = max(now, self._last_request_at + self._min_interval)
            self._last_request_at = slot
        if slot > now:
            self._sleep(slot - now)
