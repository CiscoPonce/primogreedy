"""Shared Finnhub HTTP client.

The free tier allows 60 calls per minute. A hunt scores every candidate,
and four regions run at once, so a bare ``requests.get`` trips 429s and
the insider signal is dropped. This spaces calls across threads, caches
a successful response for the rest of the process, and retries a 429
before giving up.
"""

from __future__ import annotations

import random
import threading
import time

import requests

from .logger import get_logger

logger = get_logger(__name__)

_MIN_INTERVAL = 1.05  # ~57 calls/minute, under the free-tier cap of 60
_MAX_RETRIES = 3
_CACHE_TTL = 15 * 60

_lock = threading.Lock()
_next_ok = 0.0
_cache: dict[tuple, tuple[float, int, object]] = {}


class FinnhubResponse:
    """Small stand-in for ``requests.Response`` so callers keep ``.json()``."""

    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(f"Finnhub HTTP {self.status_code}")


def finnhub_get(url: str, params: dict, timeout: int = 10) -> FinnhubResponse:
    """GET a Finnhub endpoint. ``params`` may include ``token``; it is never logged."""
    key = (
        url,
        tuple(sorted((k, str(v)) for k, v in params.items() if k != "token")),
    )
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return FinnhubResponse(hit[1], hit[2])

    last_status = None
    for attempt in range(_MAX_RETRIES):
        _pace()
        try:
            resp = requests.get(url, params=params, timeout=timeout)
        except requests.RequestException:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 429:
            last_status = 429
            if attempt == _MAX_RETRIES - 1:
                break
            wait = _retry_after(resp, attempt)
            logger.info(
                "Finnhub 429, retrying in %.1fs (attempt %d/%d)",
                wait, attempt + 1, _MAX_RETRIES,
            )
            time.sleep(wait)
            continue
        if resp.status_code >= 400:
            raise requests.HTTPError(f"Finnhub HTTP {resp.status_code}")
        payload = resp.json()
        with _lock:
            _cache[key] = (time.monotonic() + _CACHE_TTL, resp.status_code, payload)
        return FinnhubResponse(resp.status_code, payload)

    raise requests.HTTPError(f"Finnhub HTTP {last_status or 'error'}")


def _pace() -> None:
    global _next_ok
    with _lock:
        now = time.monotonic()
        delay = _next_ok - now
        _next_ok = max(now, _next_ok) + _MIN_INTERVAL
    if delay > 0:
        time.sleep(delay)


def _retry_after(resp: requests.Response, attempt: int) -> float:
    raw = resp.headers.get("Retry-After")
    try:
        return min(float(raw), 30.0)
    except (TypeError, ValueError):
        return min(30.0, (2 ** (attempt + 1)) + random.uniform(0, 0.5))
