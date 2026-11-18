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

print("\n== Debate stays on the free tier ==")

import threading
import src.agents.debate as debate

pitcher = debate.PITCHER_MODEL
skeptic_fb = debate._fallback_for(debate.SKEPTIC_MODEL)
pitcher_fb = debate._fallback_for(debate.PITCHER_MODEL)
check("skeptic backup is not the pitcher", skeptic_fb != pitcher, skeptic_fb)
check("pitcher backup is not the skeptic", pitcher_fb != debate.SKEPTIC_MODEL, pitcher_fb)

calls = []
sleeps = []


class _Msg:
    def __init__(self, content):
        self.content = content


class _LLM:
    def __init__(self, model):
        self.model = model

    def invoke(self, prompt):
        calls.append(self.model)
        if self.model == debate.SKEPTIC_MODEL:
            raise RuntimeError("429 Too Many Requests")
        return _Msg("bear case")


debate._make_llm = lambda model, max_tokens=2048: _LLM(model)
debate.time.sleep = lambda seconds: sleeps.append(seconds)
text = debate._resilient_llm_call(
    debate.SKEPTIC_MODEL, "prompt", debate._fallback_for(debate.SKEPTIC_MODEL),
)
check("429 switches after one wait", text == "bear case" and calls.count(debate.SKEPTIC_MODEL) == 1, calls)
check("429 does not camp on the same model", calls.count(debate.SKEPTIC_MODEL) < 2)
check("fallback call is the non-pitcher model", calls[-1] == skeptic_fb, calls)

overlap = {"n": 0, "max": 0}
gate = threading.Lock()


def _fake_invoke(state):
    with gate:
        overlap["n"] += 1
        overlap["max"] = max(overlap["max"], overlap["n"])
    import time as real_time
    real_time.sleep(0.05)
    with gate:
        overlap["n"] -= 1
    return {"final_verdict": "v", "bull_case": "b", "bear_case": "r", "_structured_result": None}


debate.debate_app.invoke = _fake_invoke
kwargs = dict(
    ticker="T", company_name="C", financial_data_summary="f",
    deep_fundamentals="d", sec_context="s", strategy="GRAHAM CLASSIC",
    price=1.0, eps=0.1, book_value=1.0, ebitda=1.0,
)
threads = [threading.Thread(target=debate.run_debate, kwargs=kwargs) for _ in range(2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("debates do not overlap", overlap["max"] == 1, overlap)

print()
if failures:
    print(f"{len(failures)} FAIL")
    sys.exit(1)
print("ALL PASS")
