"""Tests for the web API. We swap the real setlist.fm client for a fake one
using FastAPI's dependency_overrides, so these run instantly and offline."""

import pytest
from fastapi.testclient import TestClient

from app.main import app, get_client
from app.setlistfm import Artist, SetlistFmError
from tests.helpers import make_show

MBID = "11111111-2222-3333-4444-555555555555"


class FakeSetlistClient:
    def __init__(self, shows=None, error=None):
        self.shows = shows if shows is not None else []
        self.error = error

    def search_artists(self, name):
        return [Artist(MBID, "The Example Band", "US indie band")]

    def get_recent_shows(self, mbid, max_shows=40):
        if self.error:
            raise self.error
        return self.shows[:max_shows]


@pytest.fixture
def api():
    """Yields a function that builds a TestClient backed by a given fake."""

    def build(fake):
        app.dependency_overrides[get_client] = lambda: fake
        return TestClient(app)

    yield build
    app.dependency_overrides.clear()


def regular_shows(count=10):
    return [make_show(["Opener", "Hit", "Closer"], encore=["Encore"], days_ago=d) for d in range(count)]


def test_health(api):
    assert api(FakeSetlistClient()).get("/api/health").json() == {"status": "ok"}


def test_artist_search(api):
    response = api(FakeSetlistClient()).get("/api/artists", params={"q": "example"})
    assert response.status_code == 200
    assert response.json()[0]["mbid"] == MBID


def test_predict_returns_setlist_in_order(api):
    response = api(FakeSetlistClient(regular_shows())).get("/api/predict", params={"mbid": MBID})
    assert response.status_code == 200
    body = response.json()
    assert [s["name"] for s in body["setlist"]] == ["Opener", "Hit", "Closer", "Encore"]
    assert body["setlist"][0]["category"] == "lock"
    assert body["shows_used"] == 10


def test_predict_rejects_malformed_mbid(api):
    response = api(FakeSetlistClient()).get("/api/predict", params={"mbid": "not-an-id"})
    assert response.status_code == 422  # FastAPI's "invalid input" status


def test_predict_404_when_artist_has_no_setlists(api):
    response = api(FakeSetlistClient([])).get("/api/predict", params={"mbid": MBID})
    assert response.status_code == 404


def test_upstream_failure_becomes_502(api):
    fake = FakeSetlistClient(error=SetlistFmError("setlist.fm is down"))
    response = api(fake).get("/api/predict", params={"mbid": MBID})
    assert response.status_code == 502
    assert response.json()["detail"] == "setlist.fm is down"


def test_backtest_reports_both_scores(api):
    body = api(FakeSetlistClient(regular_shows())).get("/api/backtest", params={"mbid": MBID}).json()
    assert body["precision"] == 1.0
    assert body["baseline_precision"] == 1.0
    assert body["shows_tested"] == 5


def test_missing_api_key_gives_clear_error(monkeypatch):
    monkeypatch.delenv("SETLISTFM_API_KEY", raising=False)
    response = TestClient(app).get("/api/artists", params={"q": "example"})
    assert response.status_code == 503
    assert "SETLISTFM_API_KEY" in response.json()["detail"]
