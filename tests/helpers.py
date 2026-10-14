"""Shared helpers for the tests: loading fixtures and building fake shows."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from app.setlistfm import Show, SongPlay

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def make_show(
    songs: list[str],
    days_ago: int = 0,
    encore: list[str] | None = None,
) -> Show:
    """Build a fake Show quickly, e.g. make_show(["A", "B"], days_ago=3, encore=["C"])."""
    encore = encore or []
    plays = [SongPlay(name, i, False, False) for i, name in enumerate(songs)]
    plays += [SongPlay(name, len(songs) + i, True, False) for i, name in enumerate(encore)]
    return Show(
        id=f"show-{days_ago}",
        date=date(2026, 10, 1) - timedelta(days=days_ago),
        venue="Test Venue",
        city="Test City",
        tour=None,
        songs=tuple(plays),
    )
