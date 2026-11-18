"""Network-free checks for LangSmith feedback and content-block text.

Run with:  python tests/test_online_eval.py
"""

import importlib.util
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["LANGCHAIN_API_KEY"] = "test-key-not-real"
os.environ["LANGCHAIN_TRACING_V2"] = "true"

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


print("\n== message_text ==")

from src.llm import message_text

check("string passes through", message_text("BUY") == "BUY")
check("none is empty", message_text(None) == "")
check("empty list is empty", message_text([]) == "")
check("text block", message_text([{"type": "text", "text": "BUY"}]) == "BUY")
check("refusal block", message_text([{"type": "refusal", "refusal": "no"}]) == "no")
check(
    "mixed blocks concatenate",
    message_text([
        {"type": "text", "text": "BUY"},
        {"type": "refusal", "refusal": "no"},
    ]) == "BUYno",
)


print("\n== online feedback ==")

import langsmith
from src.core import online_eval


class _Tree:
    def __init__(self, run_id, session_id):
        self.id = run_id
        self.session_id = session_id


class _Client:
    calls = []
    raise_on_feedback = False

    def __init__(self, *args, **kwargs):
        pass

    def create_feedback(self, **kwargs):
        if _Client.raise_on_feedback:
            raise RuntimeError("nope")
        _Client.calls.append(kwargs)

    def read_project(self, **kwargs):
        raise AssertionError("read_project should not be called")


def _install_fakes(tree):
    _Client.calls = []
    _Client.raise_on_feedback = False
    online_eval._cached_session_id = None
    langsmith.Client = _Client
    langsmith.get_current_run_tree = lambda: tree


_install_fakes(_Tree("run-1", "sess-1"))
online_eval.log_online_feedback("### FINAL VERDICT\nBUY", "AAPL", run_id="run-1")
check("feedback called twice", len(_Client.calls) == 2, f"n={len(_Client.calls)}")
check(
    "feedback carries run and session",
    all(c.get("run_id") == "run-1" and c.get("session_id") == "sess-1" for c in _Client.calls),
    str(_Client.calls),
)

_install_fakes(_Tree("run-1", "sess-1"))
online_eval.log_online_feedback("### FINAL VERDICT\nBUY", "AAPL", run_id=None)
check("missing run id skips feedback", _Client.calls == [])

_install_fakes(_Tree("run-1", "sess-1"))
_Client.raise_on_feedback = True
try:
    online_eval.log_online_feedback("### FINAL VERDICT\nBUY", "AAPL", run_id="run-1")
    check("client error does not raise", True)
except Exception as exc:
    check("client error does not raise", False, type(exc).__name__)


print("\n== smithdb query kwargs ==")

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "build_golden_dataset",
    os.path.join(_root, "scripts", "build_golden_dataset.py"),
)
_golden = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_golden)

_since = datetime.utcnow() - timedelta(days=30)
_kw = _golden.query_kwargs("abc", _since, 150)
_age = datetime.utcnow() - _kw["min_start_time"]
check("run type is CHAIN", _kw["run_type"] == "CHAIN")
check("project id list", _kw["project_ids"] == ["abc"])
check("selects inputs", "INPUTS" in _kw["selects"])
check("selects outputs", "OUTPUTS" in _kw["selects"])
check(
    "window is about 30 days",
    abs(_age - timedelta(days=30)) < timedelta(minutes=2),
    str(_age),
)

print(f"\n{PASSED} passed, {FAILED} failed")
if FAILED:
    sys.exit(1)
