import pytest

from app.predictor import (
    LIKELY,
    LOCK,
    RARE,
    ROTATING,
    categorize,
    expected_length,
    predict,
    recency_weights,
    song_stats,
)
from tests.helpers import make_show


def stats_by_name(shows, **kwargs):
    return {s.name: s for s in song_stats(shows, **kwargs)}


# ---- recency weights -------------------------------------------------------


def test_weights_halve_every_half_life():
    weights = recency_weights(5, half_life=2)
    assert weights[0] == 1.0
    assert weights[2] == pytest.approx(0.5)
    assert weights[4] == pytest.approx(0.25)


def test_no_half_life_means_equal_weights():
    assert recency_weights(3, half_life=None) == [1.0, 1.0, 1.0]


def test_rejects_non_positive_half_life():
    with pytest.raises(ValueError):
        recency_weights(3, half_life=0)


# ---- categories ------------------------------------------------------------


@pytest.mark.parametrize(
    "probability, expected",
    [(1.0, LOCK), (0.8, LOCK), (0.6, LIKELY), (0.2, ROTATING), (0.05, RARE)],
)
def test_categorize(probability, expected):
    assert categorize(probability) == expected


# ---- song statistics -------------------------------------------------------


def test_song_played_every_night_is_a_lock():
    shows = [make_show(["Hit", "Other"], days_ago=d) for d in range(5)]
    hit = stats_by_name(shows)["Hit"]
    assert hit.probability == pytest.approx(1.0)
    assert hit.category == LOCK
    assert hit.times_played == 5


def test_unweighted_probability_is_plain_share_of_shows():
    shows = [
        make_show(["A", "B"], days_ago=0),
        make_show(["A"], days_ago=1),
        make_show(["A"], days_ago=2),
        make_show(["A", "B"], days_ago=3),
    ]
    assert stats_by_name(shows, half_life=None)["B"].probability == pytest.approx(0.5)


def test_recent_plays_count_more_than_old_ones():
    # "New" was played only at the 2 newest shows, "Old" only at the 2 oldest.
    shows = [
        make_show(["New"], days_ago=0),
        make_show(["New"], days_ago=1),
        make_show(["Old"], days_ago=2),
        make_show(["Old"], days_ago=3),
    ]
    stats = stats_by_name(shows, half_life=2)
    assert stats["New"].probability > stats["Old"].probability


def test_reprised_song_is_counted_once_per_show():
    shows = [make_show(["Theme", "Middle", "Theme"])]
    theme = stats_by_name(shows)["Theme"]
    assert theme.probability == pytest.approx(1.0)
    assert theme.times_played == 1


def test_average_position_is_relative():
    shows = [make_show(["First", "Middle", "Last"])]
    stats = stats_by_name(shows)
    assert stats["First"].avg_position == 0.0
    assert stats["Last"].avg_position == 1.0


def test_encore_rate():
    shows = [
        make_show(["A"], encore=["Closer"], days_ago=0),
        make_show(["A", "Closer"], days_ago=1),
    ]
    assert stats_by_name(shows)["Closer"].encore_rate == 0.5


def test_last_played_and_shows_since():
    shows = [
        make_show(["Every Night"], days_ago=0),
        make_show(["Every Night"], days_ago=1),
        make_show(["Every Night", "Deep Cut"], days_ago=2),
    ]
    stats = stats_by_name(shows)
    assert stats["Every Night"].shows_since == 0
    assert stats["Every Night"].last_played == shows[0].date
    # Skipped at the 2 newest shows, last played 2 days back.
    assert stats["Deep Cut"].shows_since == 2
    assert stats["Deep Cut"].last_played == shows[2].date


def test_no_shows_means_no_stats():
    assert song_stats([]) == []


# ---- full prediction -------------------------------------------------------


def test_expected_length_is_average_show_size():
    shows = [make_show(["A", "B", "C"], days_ago=0), make_show(["A", "B", "C", "D", "E"], days_ago=1)]
    assert expected_length(shows, half_life=None) == 4


def test_prediction_orders_main_set_then_encore():
    shows = [
        make_show(["Opener", "Middle", "Closer"], encore=["Encore Song"], days_ago=d)
        for d in range(4)
    ]
    prediction = predict(shows)
    assert [s.name for s in prediction.setlist] == [
        "Opener", "Middle", "Closer", "Encore Song",
    ]


def test_prediction_picks_most_likely_songs():
    shows = [
        make_show(["Always1", "Always2", "Sometimes"], days_ago=0),
        make_show(["Always1", "Always2", "Rare"], days_ago=1),
        make_show(["Always1", "Always2", "Sometimes"], days_ago=2),
    ]
    names = [s.name for s in predict(shows).setlist]
    assert len(names) == 3
    assert {"Always1", "Always2", "Sometimes"} == set(names)


def test_predict_requires_shows():
    with pytest.raises(ValueError):
        predict([])


# ---- spelling variants -----------------------------------------------------


def test_spelling_variants_count_as_one_song():
    shows = [
        make_show(["Misery Business", "Don’t Stop"], days_ago=0),
        make_show(["misery  business", "Don't Stop"], days_ago=1),
    ]
    stats = stats_by_name(shows, half_life=None)
    # One entry per song, shown with the newest show's spelling, played at both shows.
    assert set(stats) == {"Misery Business", "Don’t Stop"}
    assert stats["Misery Business"].probability == 1.0
    assert stats["Misery Business"].times_played == 2


def test_backtest_matches_spelling_variants():
    from app.backtest import backtest

    history = [make_show(["Hit Song"], days_ago=d) for d in range(1, 6)]
    target = make_show(["hit song"], days_ago=0)
    result = backtest(history + [target], min_history=5)
    assert result.precision == 1.0
