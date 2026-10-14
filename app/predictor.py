"""The prediction engine.

Every function here is *pure*: it takes shows in and returns results out, with
no network calls, no database and no randomness. Same input, same output,
every time. That's what makes it easy to test and easy to explain.

The core idea
-------------
A song's probability = (weighted number of recent shows that included it)
                       / (total weight of all recent shows)

Recent shows get more weight than older ones, because bands change their
setlists over a tour. The weight halves every `half_life` shows:

    newest show   -> weight 1.0
    half_life back -> weight 0.5
    2x half_life back -> weight 0.25 ... and so on.

Passing half_life=None turns weighting off (every show counts equally),
which the backtester uses as a baseline to prove weighting actually helps.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from app.setlistfm import Show

DEFAULT_HALF_LIFE = 8.0

# Category thresholds, by probability.
LOCK = "lock"  # played at nearly every show
LIKELY = "likely"
ROTATING = "rotating"  # swaps in and out night to night
RARE = "rare"


@dataclass(frozen=True)
class SongStats:
    name: str
    probability: float  # 0.0-1.0, weighted share of recent shows with this song
    times_played: int  # raw count across the shows we looked at
    avg_position: float  # 0.0 = usually the opener, 1.0 = usually the closer
    encore_rate: float  # share of plays that were in the encore
    is_cover: bool
    category: str


@dataclass(frozen=True)
class Prediction:
    setlist: list[SongStats]  # the predicted show, in running order
    all_songs: list[SongStats] = field(repr=False)  # every song seen, most likely first
    shows_used: int = 0
    expected_length: int = 0


def recency_weights(count: int, half_life: float | None) -> list[float]:
    """Weights for `count` shows ordered newest-first."""
    if half_life is None:
        return [1.0] * count
    if half_life <= 0:
        raise ValueError("half_life must be positive")
    return [0.5 ** (i / half_life) for i in range(count)]


def categorize(probability: float) -> str:
    if probability >= 0.8:
        return LOCK
    if probability >= 0.4:
        return LIKELY
    if probability >= 0.15:
        return ROTATING
    return RARE


def song_stats(
    shows: list[Show], half_life: float | None = DEFAULT_HALF_LIFE
) -> list[SongStats]:
    """Statistics for every song played across `shows`, most likely first."""
    if not shows:
        return []

    newest_first = sorted(shows, key=lambda s: s.date, reverse=True)
    weights = recency_weights(len(newest_first), half_life)
    total_weight = sum(weights)

    weighted_plays: dict[str, float] = defaultdict(float)
    play_counts: dict[str, int] = defaultdict(int)
    positions: dict[str, list[float]] = defaultdict(list)
    encore_plays: dict[str, int] = defaultdict(int)
    covers: set[str] = set()

    for show, weight in zip(newest_first, weights):
        last_index = len(show.songs) - 1
        seen_tonight: set[str] = set()
        for song in show.songs:
            # Some bands reprise a song twice in one night. Count it once per
            # show, or its probability could go above 100%.
            if song.name in seen_tonight:
                continue
            seen_tonight.add(song.name)

            weighted_plays[song.name] += weight
            play_counts[song.name] += 1
            # Relative position, so a 12-song show and a 25-song show compare fairly.
            positions[song.name].append(
                song.position / last_index if last_index > 0 else 0.0
            )
            if song.is_encore:
                encore_plays[song.name] += 1
            if song.is_cover:
                covers.add(song.name)

    stats = []
    for name, played_weight in weighted_plays.items():
        probability = played_weight / total_weight
        count = play_counts[name]
        stats.append(
            SongStats(
                name=name,
                probability=probability,
                times_played=count,
                avg_position=sum(positions[name]) / count,
                encore_rate=encore_plays[name] / count,
                is_cover=name in covers,
                category=categorize(probability),
            )
        )

    # Most likely first; ties broken by position, then name, so results are stable.
    stats.sort(key=lambda s: (-s.probability, s.avg_position, s.name))
    return stats


def expected_length(shows: list[Show], half_life: float | None = DEFAULT_HALF_LIFE) -> int:
    """Weighted average number of distinct songs per show, rounded."""
    newest_first = sorted(shows, key=lambda s: s.date, reverse=True)
    weights = recency_weights(len(newest_first), half_life)
    weighted_total = sum(len(show.song_names) * w for show, w in zip(newest_first, weights))
    return max(1, round(weighted_total / sum(weights)))


def predict(
    shows: list[Show], half_life: float | None = DEFAULT_HALF_LIFE
) -> Prediction:
    """Predict the next show's setlist from past shows.

    1. Score every song (song_stats).
    2. Decide how many songs the show will have (expected_length).
    3. Take that many of the most likely songs.
    4. Put them in running order: main set by usual position, then encore songs.
    """
    if not shows:
        raise ValueError("Need at least one show with a setlist to predict from.")

    stats = song_stats(shows, half_life)
    length = expected_length(shows, half_life)
    chosen = stats[:length]

    encore = [s for s in chosen if s.encore_rate >= 0.5]
    main_set = [s for s in chosen if s.encore_rate < 0.5]
    in_order = sorted(main_set, key=lambda s: s.avg_position) + sorted(
        encore, key=lambda s: s.avg_position
    )

    return Prediction(
        setlist=in_order,
        all_songs=stats,
        shows_used=len(shows),
        expected_length=length,
    )
