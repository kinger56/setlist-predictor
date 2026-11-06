"""Rate limiting, security headers and real-IP handling."""

import base64
import hashlib
import re

from fastapi.testclient import TestClient

from app.client_ip import real_client_ip
from app.main import API_REQUESTS_PER_WINDOW, STATIC_DIR, app, get_client
from app.throttle import Throttle
from tests.test_api import FakeSetlistClient

RENDER_PROXY = "10.12.0.5"


def fresh_client():
    app.dependency_overrides[get_client] = lambda: FakeSetlistClient()
    app.state.api_throttle = Throttle(API_REQUESTS_PER_WINDOW, 300)
    return TestClient(app, client=(RENDER_PROXY, 1234))


def search(client, real_ip, fake_ip="45.33.32.1"):
    return client.get(
        "/api/artists", params={"q": "x"}, headers={"X-Forwarded-For": f"{fake_ip}, {real_ip}"}
    )


def test_api_is_rate_limited_per_real_visitor():
    client = fresh_client()
    for i in range(API_REQUESTS_PER_WINDOW):
        assert search(client, "73.15.20.9", fake_ip=f"45.33.32.{i}").status_code == 200
    blocked = search(client, "73.15.20.9", fake_ip="45.33.32.250")  # faking a new IP doesn't help
    assert blocked.status_code == 429 and "retry-after" in blocked.headers
    assert search(client, "73.15.20.10").status_code == 200  # other visitors are fine
    app.dependency_overrides.clear()


def test_health_check_is_never_rate_limited():
    client = fresh_client()
    app.state.api_throttle = Throttle(0, 300)  # everything else would be blocked
    assert client.get("/api/health").status_code == 200
    app.dependency_overrides.clear()


def test_security_headers_on_the_page():
    headers = TestClient(app).get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    csp = headers["content-security-policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp


def test_csp_allows_exactly_the_pages_own_inline_code():
    # If index.html's <script> or <style> changes, the hashes must too, or
    # the page would break. They're computed at startup, so they always match.
    page = (STATIC_DIR / "index.html").read_text()
    csp = TestClient(app).get("/").headers["content-security-policy"]
    for tag in ("script", "style"):
        for block in re.findall(rf"<{tag}>(.*?)</{tag}>", page, flags=re.DOTALL):
            digest = base64.b64encode(hashlib.sha256(block.encode()).digest()).decode()
            assert f"'sha256-{digest}'" in csp


def test_fake_forwarded_ips_are_ignored():
    assert real_client_ip("45.33.32.1, 73.15.20.9", RENDER_PROXY) == "73.15.20.9"


def test_render_turns_off_uvicorns_own_header_handling():
    from pathlib import Path
    start = (Path(__file__).parent.parent / "render.yaml").read_text()
    assert "--no-proxy-headers" in start and " --proxy-headers" not in start


def test_header_chain_seen_on_render():
    # Captured from Render (2026-11-06): visitor, Cloudflare edge, Render internal.
    chain = "45.33.32.250,129.210.115.225, 104.23.160.121, 10.31.99.80"
    assert real_client_ip(chain, "127.0.0.1") == "129.210.115.225"
