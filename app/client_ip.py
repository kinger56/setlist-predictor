"""Finding the visitor's real IP address behind Render's proxies.

Requests reach the app through Cloudflare and Render's load balancer.
Each proxy *appends* the address it received the request from to the
X-Forwarded-For header, so it looks like:

    X-Forwarded-For: <anything the visitor typed>, <real visitor IP>, <proxy IPs...>

The leftmost entries are written by the visitor and can't be trusted:
an attacker can put a fake IP there on every request to dodge per-IP
rate limits. So we read the header from the RIGHT and skip addresses
that belong to our own proxies (Cloudflare's published ranges and
private network addresses). The first address left is the one the
outermost trusted proxy actually saw: the real visitor.
"""

from __future__ import annotations

import ipaddress

from fastapi import Request

# https://www.cloudflare.com/ips/ (fetched 2026-11-06; changes rarely).
CLOUDFLARE_RANGES = [
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
]
_TRUSTED_PROXY_NETWORKS = [ipaddress.ip_network(r) for r in CLOUDFLARE_RANGES]


def _is_trusted_proxy(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    # Private/loopback addresses are Render's internal network (or this
    # machine during development), never a real visitor on the internet.
    if ip.is_private or ip.is_loopback:
        return True
    return any(ip in network for network in _TRUSTED_PROXY_NETWORKS)


def real_client_ip(x_forwarded_for: str | None, peer: str | None) -> str:
    """The visitor's IP, given the X-Forwarded-For header and the address
    of whoever connected to us directly (`peer`)."""
    hops = [h.strip() for h in (x_forwarded_for or "").split(",") if h.strip()]
    for hop in reversed([*hops, peer or ""]):
        if hop and not _is_trusted_proxy(hop):
            return hop
    # Every hop was one of ours (e.g. local development): use the nearest.
    return peer or "unknown"


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else None
    return real_client_ip(request.headers.get("x-forwarded-for"), peer)
