from datetime import date

from app.setlistfm import parse_setlist
from tests.helpers import load_fixture

RAW = load_fixture("artist_setlists.json")["setlist"]


def test_parses_basic_show_details():
    show = parse_setlist(RAW[0])
    assert show.date == date(2026, 9, 18)
    assert show.venue == "The Fillmore"
    assert show.city == "San Francisco"
    assert show.tour == "Fake Tour 2026"


def test_skips_tape_songs_and_blank_names():
    show = parse_setlist(RAW[0])
    assert [s.name for s in show.songs] == [
        "Opener Song", "Hit Single", "Old Favorite", "Big Closer",
    ]


def test_positions_count_only_real_songs():
    show = parse_setlist(RAW[0])
    assert [s.position for s in show.songs] == [0, 1, 2, 3]


def test_marks_encores_and_covers():
    songs = {s.name: s for s in parse_setlist(RAW[0]).songs}
    assert songs["Big Closer"].is_encore
    assert not songs["Hit Single"].is_encore
    assert songs["Old Favorite"].is_cover
    assert not songs["Opener Song"].is_cover


def test_show_without_songs_is_skipped():
    # Upcoming or not-yet-filled-in shows have no songs.
    assert parse_setlist(RAW[1]) is None


def test_missing_tour_is_none():
    assert parse_setlist(RAW[2]).tour is None
