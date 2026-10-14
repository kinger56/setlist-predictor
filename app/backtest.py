"""Backtesting: how accurate are the predictions, really?

We "travel back in time": for each past show, we predict it using ONLY the
shows that came before it, then compare the prediction with what the band
actually played. Averaging over many shows gives an honest accuracy score.

Using only earlier shows matters. If the show we're predicting were included
in its own input, the model would be peeking at the answer ("data leakage")
and the score would look better than it really is.

Two scores:
  precision = of the songs we predicted, what share were actually played?
  recall    = of the songs actually played, what share did we predict?
"""

from __future__ import annotations

from dataclasses import dataclass

from app.predictor import DEFAULT_HALF_LIFE, predict
from app.setlistfm import Show


@dataclass(frozen=True)
class BacktestResult:
    shows_tested: int
    precision: float
    recall: float
    half_life: float | None


def score(predicted: set[str], actual: set[str]) -> tuple[float, float]:
    """(precision, recall) for one predicted show."""
    hits = len(predicted & actual)
    precision = hits / len(predicted) if predicted else 0.0
    recall = hits / len(actual) if actual else 0.0
    return precision, recall


def backtest(
    shows: list[Show],
    history_size: int = 20,
    min_history: int = 5,
    half_life: float | None = DEFAULT_HALF_LIFE,
) -> BacktestResult:
    """Predict each show from the up-to-`history_size` shows before it."""
    oldest_first = sorted(shows, key=lambda s: s.date)

    precisions: list[float] = []
    recalls: list[float] = []
    # Start at `min_history` so every prediction has enough history behind it.
    for i in range(min_history, len(oldest_first)):
        history = oldest_first[max(0, i - history_size) : i]
        target = oldest_first[i]

        prediction = predict(history, half_life)
        predicted_names = {song.name for song in prediction.setlist}
        p, r = score(predicted_names, target.song_names)
        precisions.append(p)
        recalls.append(r)

    if not precisions:
        raise ValueError(
            f"Need at least {min_history + 1} shows to backtest; got {len(shows)}."
        )

    return BacktestResult(
        shows_tested=len(precisions),
        precision=sum(precisions) / len(precisions),
        recall=sum(recalls) / len(recalls),
        half_life=half_life,
    )
