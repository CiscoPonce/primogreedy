"""Network-free checks for the Finnhub throttle and the VPS HTTPS retry."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("FINNHUB_API_KEY", "test-key")
os.environ["VPS_API_URL"] = "http://example.test:8080"
os.environ["VPS_API_KEY"] = "test-key"

failures = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        failures.append(name)


class _Resp:
    def __init__(self, status, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.content = content
        self.headers = headers or {}
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise RuntimeError(f"status {self.status_code}")


print("\n== Finnhub 429 retry and cache ==")

import src.core.finnhub_http as fh

calls = []
sleeps = []


def fake_get(url, params=None, timeout=10):
    calls.append((url, dict(params or {})))
    if len(calls) == 1:
        return _Resp(429, headers={"Retry-After": "1"})
    return _Resp(200, {"data": [{"share": 10}]})


def fake_sleep(seconds):
    sleeps.append(seconds)


fh.requests.get = fake_get
fh.time.sleep = fake_sleep
fh._cache.clear()
fh._next_ok = 0.0

resp = fh.finnhub_get(
    "https://finnhub.io/api/v1/stock/insider-transactions",
    params={"symbol": "SPCE", "token": "secret-token"},
)
check("retries a 429 then returns the body", resp.json() == {"data": [{"share": 10}]})
check("second attempt is the one that succeeds", len(calls) == 2, len(calls))
check("request carries the token as a param", all(c[1].get("token") == "secret-token" for c in calls))
check("honours Retry-After", 1.0 in sleeps, sleeps)

again = fh.finnhub_get(
    "https://finnhub.io/api/v1/stock/insider-transactions",
    params={"symbol": "SPCE", "token": "secret-token"},
)
check("caches a successful response", again.json()["data"][0]["share"] == 10 and len(calls) == 2)

try:
    fh._cache.clear()
    fh.requests.get = lambda *a, **k: _Resp(429, headers={})
    fh.finnhub_get("https://finnhub.io/api/v1/stock/insider-transactions", params={"symbol": "X"})
    check("gives up after repeated 429s", False)
except Exception as exc:
    check("gives up after repeated 429s", "429" in str(exc), str(exc))
    check("error text has no token", "secret-token" not in str(exc), str(exc))

print("\n== VPS http port is really HTTPS ==")

import importlib
import src.core.memory as memory

memory = importlib.reload(memory)

seen = []


def fake_request(method, url, headers=None, timeout=5, verify=True, json=None):
    seen.append((method, url, verify, json))
    if url.startswith("http://"):
        return _Resp(400, content=b"Client sent an HTTP request to an HTTPS server.\n")
    return _Resp(200, {"status": "ok", "ticker": "SPCE"})


memory.requests.request = fake_request
memory.mark_ticker_seen("OPSFIX", "USA")
check("plain HTTP 400 is retried over HTTPS", len(seen) == 2, seen)
check(
    "retry keeps the path and the body",
    seen[1][0] == "POST"
    and seen[1][1].startswith("https://example.test:8080/seen-tickers")
    and seen[1][3]["ticker"] == "OPSFIX",
    seen,
)
local = ""
if os.path.exists("seen_tickers.json"):
    local = open("seen_tickers.json").read()
check("local ledger is not used when HTTPS succeeds", "OPSFIX" not in local)

print()
if failures:
    print(f"{len(failures)} FAIL")
    sys.exit(1)
print("ALL PASS")
