"""Compare recency settings across several artists using backtesting.

This is how you find out whether recency weighting actually helps, and which
half-life works best. Use the output for your README and resume numbers.

Usage (from the project root):
    python -m scripts.tune "Paramore" "Phish" "Taylor Swift" "Bleachers"
"""

import argparse
import os

from dotenv import load_dotenv

from app.backtest import backtest
from app.cache import Cache
from app.setlistfm import SetlistFmClient

HALF_LIVES = [None, 2.0, 4.0, 8.0, 16.0]  # None = every show counts equally


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artists", nargs="+")
    parser.add_argument("--shows", type=int, default=60)
    args = parser.parse_args()

    load_dotenv()
    client = SetlistFmClient(
        os.getenv("SETLISTFM_API_KEY", ""), cache=Cache(os.getenv("CACHE_DB", "cache.db"))
    )

    header = f"{'artist':<24}{'shows':>7}" + "".join(
        f"{('equal' if h is None else f'hl={h:g}'):>10}" for h in HALF_LIVES
    )
    print("Precision by recency half-life\n")
    print(header)
    print("-" * len(header))

    totals = {h: [] for h in HALF_LIVES}
    for name in args.artists:
        found = client.search_artists(name)
        if not found:
            print(f"{name:<24}  not found")
            continue
        shows = client.get_recent_shows(found[0].mbid, max_shows=args.shows)
        try:
            results = [backtest(shows, half_life=h) for h in HALF_LIVES]
        except ValueError as exc:
            print(f"{name:<24}  {exc}")
            continue
        for h, r in zip(HALF_LIVES, results):
            totals[h].append((r.precision, r.shows_tested))
        row = f"{found[0].name[:23]:<24}{results[0].shows_tested:>7}"
        print(row + "".join(f"{r.precision:>10.1%}" for r in results))

    # Average weighted by shows tested, so big samples count more.
    if any(totals.values()):
        print("-" * len(header))
        row = f"{'overall':<24}{sum(n for _, n in totals[None]):>7}"
        for h in HALF_LIVES:
            tested = sum(n for _, n in totals[h])
            avg = sum(p * n for p, n in totals[h]) / tested if tested else 0
            row += f"{avg:>10.1%}"
        print(row)


if __name__ == "__main__":
    main()
