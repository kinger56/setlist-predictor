"""Tests for the setlist.fm client, using a fake HTTP client.

The fake records every request and replies with responses we queue up in
advance. That lets us test retries, errors and caching with no network.
"""

import json

import httpx
import pytest

from app.setlistfm import SetlistFmClient, SetlistFmError
from tests.helpers import load_fixture


class FakeResponse:
    def __init__(self, status_code: int, body: dict | None = None):
        self.status_code = status_code
        self.text = json.dumps(body or {})

    def json(self):
        return json.loads(self.text)


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def get(self, path, params=None, headers=None):
        self.requests.append({"path": path, "params": params, "headers": headers})
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(responses, cache=None):
    http = FakeHttp(responses)
    sleeps = []
    client = SetlistFmClient(
        "test-key", cache=cache, http=http, sleep=sleeps.append, min_interval=0
    )
    return client, http, sleeps


ARTIST_SEARCH = {
    "artist": [
        {"mbid": "11111111-2222-3333-4444-555555555555", "name": "The Example Band",
         "disambiguation": "US indie band"},
    ]
}


def test_sends_api_key_and_asks_for_json():
    client, http, _ = make_client([FakeResponse(200, ARTIST_SEARCH)])
    client.search_artists("example")
    headers = http.requests[0]["headers"]
    assert headers["x-api-key"] == "test-key"
    assert headers["Accept"] == "application/json"


def test_search_returns_artists():
    client, _, _ = make_client([FakeResponse(200, ARTIST_SEARCH)])
    [artist] = client.search_artists("example")
    assert artist.name == "The Example Band"
    assert artist.disambiguation == "US indie band"


def test_404_means_no_results():
    client, _, _ = make_client([FakeResponse(404)])
    assert client.search_artists("zzzz") == []


def test_retries_after_rate_limit_then_succeeds():
    client, http, sleeps = make_client([FakeResponse(429), FakeResponse(200, ARTIST_SEARCH)])
    assert len(client.search_artists("example")) == 1
    assert len(http.requests) == 2
    assert sleeps == [1]  # backed off for 1 second before retrying


def test_retries_after_network_error():
    client, http, _ = make_client(
        [httpx.ConnectError("boom"), FakeResponse(200, ARTIST_SEARCH)]
    )
    assert len(client.search_artists("example")) == 1


def test_backoff_doubles_and_eventually_gives_up():
    client, http, sleeps = make_client([FakeResponse(503)] * 4)
    with pytest.raises(SetlistFmError):
        client.search_artists("example")
    assert len(http.requests) == 4
    assert sleeps == [1, 2, 4]


def test_does_not_retry_bad_api_key():
    client, http, _ = make_client([FakeResponse(403)])
    with pytest.raises(SetlistFmError):
        client.search_artists("example")
    assert len(http.requests) == 1


def test_cached_responses_skip_the_network(make_cache):
    cache = make_cache()
    client, http, _ = make_client([FakeResponse(200, ARTIST_SEARCH)], cache=cache)
    client.search_artists("example")
    client.search_artists("example")
    assert len(http.requests) == 1


def test_recent_shows_skips_empty_setlists_and_sorts_newest_first():
    client, _, _ = make_client([FakeResponse(200, load_fixture("artist_setlists.json"))])
    shows = client.get_recent_shows("11111111-2222-3333-4444-555555555555")
    assert [s.venue for s in shows] == ["The Fillmore", "Roseland"]


def test_recent_shows_follows_pages():
    page = load_fixture("artist_setlists.json")
    page["total"] = 6
    page["itemsPerPage"] = 3
    client, http, _ = make_client([FakeResponse(200, page), FakeResponse(200, page)])
    shows = client.get_recent_shows("11111111-2222-3333-4444-555555555555")
    assert [r["params"]["p"] for r in http.requests] == [1, 2]
    assert len(shows) == 4


def test_requires_api_key():
    with pytest.raises(SetlistFmError):
        SetlistFmClient("")


def test_rate_limiter_gives_concurrent_requests_separate_slots():
    # Five server threads ask for a turn at the same instant. Each must get
    # its own slot, min_interval apart, instead of all going at once.
    import threading

    sleeps = []
    client = SetlistFmClient(
        "test-key", http=FakeHttp([]), sleep=sleeps.append, min_interval=10
    )
    start = threading.Barrier(5)

    def take_turn():
        start.wait()
        client._wait_for_turn()

    threads = [threading.Thread(target=take_turn) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # One thread goes immediately; the others wait about 10, 20, 30 and 40s.
    assert len(sleeps) == 4
    for expected, actual in zip([10, 20, 30, 40], sorted(sleeps)):
        assert actual == pytest.approx(expected, abs=1)
