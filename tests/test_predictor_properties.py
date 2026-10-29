"""Property-based tests for the predictor, using Hypothesis.

Normal tests check one hand-picked example each. These describe rules that
must hold for EVERY input, and Hypothesis generates hundreds of random
setlist histories to try to break them. When it finds a failure, it shrinks
it down to the smallest example that still fails, which makes it easy to debug.
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from app.predictor import expected_length, predict, song_key
from tests.helpers import make_show

# A small pool of titles, so random shows overlap the way real tours do.
# Includes spelling variants of the same song to exercise song_key().
SONGS = [f"Song {i}" for i in range(15)] + ["song 0", "SONG 1"]

one_show = st.tuples(
    st.lists(st.sampled_from(SONGS), min_size=1, max_size=12),  # main set
    st.lists(st.sampled_from(SONGS), max_size=3),  # encore
)
show_histories = st.lists(one_show, min_size=1, max_size=15).map(
    lambda raw: [make_show(main, days_ago=i, encore=enc) for i, (main, enc) in enumerate(raw)]
)
half_lives = st.one_of(st.none(), st.floats(min_value=0.5, max_value=20))


@settings(max_examples=300)
@given(shows=show_histories, half_life=half_lives)
def test_probabilities_are_valid_and_sorted(shows, half_life):
    songs = predict(shows, half_life).all_songs
    assert all(0 < s.probability <= 1 + 1e-9 for s in songs)
    probabilities = [s.probability for s in songs]
    assert probabilities == sorted(probabilities, reverse=True)


@settings(max_examples=300)
@given(shows=show_histories, half_life=half_lives)
def test_setlist_has_expected_length_and_no_repeats(shows, half_life):
    prediction = predict(shows, half_life)
    keys = [song_key(s.name) for s in prediction.setlist]
    assert len(keys) == expected_length(shows, half_life) == prediction.expected_length
    assert len(keys) == len(set(keys))


@settings(max_examples=300)
@given(shows=show_histories, half_life=half_lives)
def test_encore_songs_come_after_main_set(shows, half_life):
    in_encore = [s.encore_rate >= 0.5 for s in predict(shows, half_life).setlist]
    # Once the encore starts, no main-set song may follow it.
    assert in_encore == sorted(in_encore)


@settings(max_examples=300)
@given(shows=show_histories, half_life=half_lives)
def test_song_played_at_every_show_is_certain(shows, half_life):
    every_night = set.intersection(*({song_key(n) for n in show.song_names} for show in shows))
    for song in predict(shows, half_life).all_songs:
        if song_key(song.name) in every_night:
            assert abs(song.probability - 1.0) < 1e-9


@settings(max_examples=300)
@given(shows=show_histories)
def test_unweighted_probability_is_share_of_shows(shows):
    for song in predict(shows, half_life=None).all_songs:
        assert abs(song.probability - song.times_played / len(shows)) < 1e-9
