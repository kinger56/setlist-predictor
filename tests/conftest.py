"""Fixtures shared by every test file (pytest loads conftest.py automatically)."""

import pytest

from app.cache import Cache


@pytest.fixture
def make_cache():
    """Build Cache objects that are closed automatically when the test ends,
    so no SQLite connections are left open."""
    caches = []

    def build(*args, **kwargs):
        cache = Cache(*args, **kwargs)
        caches.append(cache)
        return cache

    yield build
    for cache in caches:
        cache.close()
