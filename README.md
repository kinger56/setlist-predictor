# Setlist Predictor

Predicts what a band will play at your concert, based on their recent setlists from [setlist.fm](https://www.setlist.fm), and measures how accurate those predictions are on real past shows.

> Draft README. Screenshot/GIF, live demo link and accuracy results are added in week 6.

## How it works

1. Fetches the artist's most recent setlists from the setlist.fm API (cached in SQLite, rate-limited, with retries).
2. Scores every song by how often it was played, giving recent shows more weight (each show's weight halves every 8 shows back).
3. Picks the expected number of songs and orders them by where they usually appear, with encore songs last.
4. **Backtesting:** predicts each past show using only the shows before it, then compares with what was actually played.

## Tech stack

Python · FastAPI · SQLite · httpx · vanilla HTML/JS · pytest · GitHub Actions

## Run it locally

```bash
git clone https://github.com/<your-username>/setlist-predictor.git
cd setlist-predictor
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then put your setlist.fm API key in .env
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. Get a free, non-commercial API key at https://www.setlist.fm/settings/api.

Command-line tools:

```bash
python -m scripts.explore "Paramore"                  # predicted setlist + song frequencies
python -m scripts.tune "Paramore" "Phish" "Bleachers"  # accuracy for different recency settings
```

## Tests

```bash
pytest -v
```

Tests never touch the network: the API client is tested against a fake HTTP client, and the web routes against a fake setlist.fm client.

## Project structure

```
app/
  setlistfm.py   API client + parsing JSON into Show objects
  cache.py       SQLite response cache with expiry
  predictor.py   prediction logic (pure functions)
  backtest.py    accuracy measurement on past shows
  main.py        FastAPI routes
  static/        the web page
scripts/         command-line tools
tests/           pytest suite + JSON fixtures
```

## What I'd improve next

- Only use shows from the current tour when a new tour starts
- Predict song *position* more precisely (e.g. learn common song pairs)
- Show a per-song "last played" date
