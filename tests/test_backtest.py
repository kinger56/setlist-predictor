import pytest

from app.backtest import backtest, score
from tests.helpers import make_show


def test_score_precision_and_recall():
    precision, recall = score(predicted={"A", "B", "C", "D"}, actual={"A", "B", "X"})
    assert precision == pytest.approx(2 / 4)
    assert recall == pytest.approx(2 / 3)


def test_score_handles_empty_sets():
    assert score(set(), {"A"}) == (0.0, 0.0)


def test_identical_shows_are_predicted_perfectly():
    shows = [make_show(["A", "B", "C"], days_ago=d) for d in range(10)]
    result = backtest(shows, min_history=3)
    assert result.shows_tested == 7
    assert result.precision == pytest.approx(1.0)
    assert result.recall == pytest.approx(1.0)


def test_only_earlier_shows_are_used():
    # The newest show debuts a brand-new song. A fair backtest can't know it
    # was coming; if it "predicted" it, the target show leaked into the input.
    shows = [make_show(["A", "B"], days_ago=d) for d in range(1, 6)]
    shows.append(make_show(["A", "Debut"], days_ago=0))
    result = backtest(shows, min_history=5)
    assert result.shows_tested == 1
    assert result.recall == pytest.approx(0.5)  # got "A", couldn't know "Debut"


def test_needs_enough_history():
    shows = [make_show(["A"], days_ago=d) for d in range(3)]
    with pytest.raises(ValueError):
        backtest(shows, min_history=5)
