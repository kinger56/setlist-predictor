"""Explore an artist's recent setlists from the command line.

Usage (from the project root):
    python -m scripts.explore "Paramore"
    python -m scripts.explore "Paramore" --shows 30
"""

import argparse
import os

from dotenv import load_dotenv

from app.cache import Cache
from app.predictor import predict
from app.setlistfm import SetlistFmClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("artist")
    parser.add_argument("--shows", type=int, default=20)
    args = parser.parse_args()

    load_dotenv()
    client = SetlistFmClient(
        os.getenv("SETLISTFM_API_KEY", ""), cache=Cache(os.getenv("CACHE_DB", "cache.db"))
    )

    artists = client.search_artists(args.artist)
    if not artists:
        print(f"No artist found for {args.artist!r}")
        return
    artist = artists[0]
    print(f"{artist.name} ({artist.disambiguation or 'no description'})\n")

    shows = client.get_recent_shows(artist.mbid, max_shows=args.shows)
    if not shows:
        print("No setlists found.")
        return
    print(f"Using {len(shows)} shows, {shows[-1].date} to {shows[0].date}\n")

    prediction = predict(shows)
    print("Predicted setlist:")
    for i, song in enumerate(prediction.setlist, start=1):
        encore = "  [encore]" if song.encore_rate >= 0.5 else ""
        print(f"{i:>3}. {song.name:<40} {song.probability:>5.0%}  {song.category}{encore}")

    print("\nEvery song, most likely first:")
    for song in prediction.all_songs:
        print(f"  {song.times_played:>3}x  {song.probability:>5.0%}  {song.name}")


if __name__ == "__main__":
    main()
