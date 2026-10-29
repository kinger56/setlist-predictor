from app.cache import Cache


class FakeClock:
    """A clock we can move forward by hand, so we don't have to wait 12 hours."""

    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


def test_stores_and_returns_values():
    cache = Cache()
    cache.set("key", "value")
    assert cache.get("key") == "value"


def test_missing_key_returns_none():
    assert Cache().get("nope") is None


def test_entries_expire_after_ttl():
    clock = FakeClock()
    cache = Cache(ttl_seconds=60, clock=clock)
    cache.set("key", "value")

    clock.now += 59
    assert cache.get("key") == "value"

    clock.now += 2
    assert cache.get("key") is None


def test_setting_again_overwrites():
    cache = Cache()
    cache.set("key", "old")
    cache.set("key", "new")
    assert cache.get("key") == "new"


def test_prune_removes_only_expired_entries():
    clock = FakeClock()
    cache = Cache(ttl_seconds=60, clock=clock)
    cache.set("old", "value")
    clock.now += 30
    cache.set("fresh", "value")

    clock.now += 40  # "old" is now 70s old, "fresh" is 40s old
    assert cache.prune() == 1
    assert cache.get("fresh") == "value"
    assert cache.prune() == 0


def test_expired_entries_are_pruned_on_startup(tmp_path):
    path = str(tmp_path / "cache.db")
    clock = FakeClock()
    Cache(path, ttl_seconds=60, clock=clock).set("old", "value")

    clock.now += 61
    Cache(path, ttl_seconds=60, clock=clock)  # e.g. the server restarting

    import sqlite3
    rows = sqlite3.connect(path).execute("SELECT COUNT(*) FROM api_cache").fetchone()
    assert rows == (0,)
