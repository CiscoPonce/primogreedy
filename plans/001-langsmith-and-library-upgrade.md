# Plan 001: Keep the daily agent running on current LangSmith and LangChain

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**: `git diff --stat cc5a7db..HEAD -- requirements.txt src/core/online_eval.py src/llm.py src/agents/debate.py src/agents/news_intelligence_agent.py scripts/build_golden_dataset.py scripts/evaluators.py .github/workflows/hunter.yml`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: migration
- **Planned at**: commit `cc5a7db`, 2026-11-18

## Why this matters

The daily hunter and re-evaluation jobs install `requirements.txt` from scratch on GitHub Actions. That file pins `langsmith==0.7.9` (released 2026-02-27). A dry-run on 2026-11-18 showed pip trying current packages and then walking backwards until it could satisfy the pin. The install that actually succeeds is `langsmith` 0.7.9, `langchain` 1.4.6, and `langchain-core` 1.6.10. Current releases the same day were `langsmith` 0.15.0, `langchain` 1.5.1, and `langchain-core` 1.8.3. `langchain-core` 1.7 and later requires `langsmith>=0.14.9`, so the pin is what freezes the stack.

LangSmith Cloud removes the old run-query endpoints on 2027-01-31. This repo's daily jobs do not call those endpoints. They write traces with `POST /runs/multipart`, which stays. Two calls do need a change: verdict scores are posted with `create_feedback` and no project id, and the manual golden-dataset script still calls `list_runs` (`POST /api/v1/runs/query`). Feedback keeps the same method name, but `session_id` is now required. Failures are logged at debug, so scores can stop and nobody sees it.

This fork came from [ivebotunac/PrimoAgent](https://github.com/ivebotunac/PrimoAgent) (MIT, copyright 2025 Ive Botunac, see `LICENSE`). Upstream's last code commit is `95d9b45` on 2025-10-05 ("some new tests"). The 2026-01-12 commit is a readme edit. There is no newer upstream agent to merge. The original repo's benefit that still lives here is the linear four-agent workflow in `src/workflows/workflow.py` (data collection, technical analysis, news intelligence, portfolio manager) plus `src/backtesting/`. This plan keeps that graph importable on the new libraries. It does not merge upstream and it does not rewrite the hunter.

What this change is for:

- The morning cron installs a current, resolvable LangChain stack instead of backtracking.
- Traces keep being recorded the same way (`LANGCHAIN_TRACING_V2` in `hunter.yml` and `reeval.yml`).
- Verdict scores keep attaching to the run, because `session_id` is sent.
- A failed score post is a warning, so it shows up in the cron log.
- The golden-dataset script, when someone runs it later, uses the SmithDB query (`client.runs.query`) with an explicit 30-day window and the input/output fields it reads.
- `create_workflow()` still builds the original four-node graph on LangGraph.

LangChain 1.5 records a model refusal as a list of content blocks. `src/llm.py` and `src/agents/debate.py` call `.strip()` on `response.content`, which throws when that value is a list. The fallback chain then treats the model as dead and skips it. This plan adds one helper that always returns a string, and uses it on every model reply the cron and the original news agent read.

What this change deliberately does not add: thread queries, annotation queues, Deep Agents, the LangChain 1.5 model router, a new evaluator, or a live call to LangSmith. Those APIs exist. This app does not need them to keep running.

## Current state

- `requirements.txt` — the file GitHub Actions installs. Line 24 is the pin that forces the backtrack. Lines 20–23 and 49–50 are open ranges that want current 1.x packages.
- `src/core/online_eval.py` — posts `format_score` and `verdict_validity` after each analyst verdict, and tags WATCH/AVOID runs. Called from `src/agent.py`, `src/whale_hunter.py`, and `src/reeval_portfolio.py`. Those callers stay as they are.
- `scripts/build_golden_dataset.py` — manual script. Not in any workflow cron. It is the only direct `list_runs` call.
- `src/llm.py` — tags each model call for LangSmith. Keep the tracing config. Add `message_text` here and use it in `invoke_with_fallback`. `invoke_structured` returns a schema object from `with_structured_output`, not an `AIMessage`. Do not change `invoke_structured`.
- `src/agents/debate.py` — `_resilient_llm_call` and `_verdict_from_plain_json` read `.content` and then call `.strip()` or return the value as text.
- `src/agents/news_intelligence_agent.py` — the inherited news agent does `str(response.content).strip()`, which turns a block list into its Python repr. Use the same helper. Do not change the prompts or the seven NLP features.
- `scripts/evaluators.py` — manual judges call `json.loads(response.content)`. Same helper. This file is not on the cron.
- `src/workflows/workflow.py` — the inherited PrimoAgent pipeline. `create_workflow()` builds a `StateGraph` with four nodes and `RetryPolicy`.
- `src/prompts/senior_broker.py` — `client.pull_prompt`. Not on the removal list. Do not change it.
- `scripts/push_prompt_to_hub.py` — REST calls to `https://api.smith.langchain.com/repos/`. Not on the removal list. Do not change it.
- `scripts/run_evals.py` — calls `langsmith.evaluate`. Not on the daily cron. Leave the call. Do not run it against the live API.
- `.github/workflows/hunter.yml` — Python 3.11, `pip install -r requirements.txt`, then `python tests/test_discovery_improvements.py` and `python tests/test_value_improvements.py`, then `PYTHONPATH=. python src/whale_hunter.py` with `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_PROJECT=primogreedy`.
- `.github/workflows/reeval.yml` — same install and the same tracing env. It does not run the logic tests. Do not add a job. Only the hunter test step gains the new test file.
- `requirements2.txt` — an old freeze that still says `langsmith==0.6.9`. Nothing installs it. Do not edit it and do not install from it.
- Tests are plain scripts with a `check(name, cond)` helper, not pytest. See `tests/test_value_improvements.py`. Match that style.
- Commit messages in this repo look like `feat(scope): summary` and `fix(scope): summary`. Example: `fix(gatekeeper): add fast_info and Finnhub fallback for marketCap`.

`requirements.txt` lines that matter:

```20:24:requirements.txt
langchain>=1.0,<2.0
langchain-community>=0.4.0,<0.5.0
langchain-core>=1.0,<2.0
langchain-text-splitters>=1.0,<2.0
langsmith==0.7.9
```

```49:50:requirements.txt
langchain-openai>=0.3
langgraph>=1.0,<2.0
```

Feedback call as it exists today. `session_id` is absent, and the failure path is `logger.debug`:

```106:126:src/core/online_eval.py
    try:
        from langsmith import Client

        client = Client()

        for ev in evals:
            client.create_feedback(
                run_id=run_id,
                key=ev["key"],
                score=ev["score"],
                comment=f"[{ticker}] {ev['comment']}",
                source_info={"type": "online_eval", "ticker": ticker},
            ) if run_id else None

            logger.info(
                "Online eval [%s] %s: %.2f — %s",
                ticker, ev["key"], ev["score"], ev["comment"],
            )

    except Exception as exc:
        logger.debug("LangSmith feedback skipped: %s", exc)
```

Run id comes from the in-process run tree. That same object carries the project UUID as `session_id` (alias `project_id` on the run tree). Read it there. Do not add a LangSmith round-trip on the verdict path when the tree already has it.

```185:192:src/core/online_eval.py
def get_current_run_id() -> str | None:
    """Attempt to retrieve the current LangSmith run ID from callback context."""
    try:
        from langsmith import get_current_run_tree
        rt = get_current_run_tree()
        return str(rt.id) if rt else None
    except Exception:
        return None
```

The deprecated query:

```66:74:scripts/build_golden_dataset.py
    since = datetime.utcnow() - timedelta(days=30)
    runs = list(
        client.list_runs(
            project_name=project_name,
            run_type="chain",
            start_time=since,
            limit=limit * 3,
        )
    )
```

Model reply as it exists today. A string works. A list of blocks does not, because `str` has `.strip()` and `list` does not:

```183:188:src/llm.py
                response = llm.invoke(prompt, config=config)
                content = (response.content or "").strip()
                if not content:
                    raise RuntimeError(f"Empty completion from {model_id}")
                logger.info("LLM response from %s (attempt %d)", model_id, attempt + 1)
                return response.content
```

```155:157:src/agents/debate.py
            content = llm.invoke(prompt).content
            if content:  # Guard against empty completions
                return content
```

```403:414:src/agents/debate.py
            raw = _make_llm(model, max_tokens=8192).invoke(json_prompt).content
        except Exception as exc:
            last_error = exc
            logger.warning("Plain-JSON judge model %s failed: %s", model, exc)
            continue
        if not raw:
            continue

        # Strip markdown fences if the model wrapped the JSON anyway.
        cleaned = raw.strip()
```

A refusal block from LangChain 1.5 looks like a dict in that list, with `type` of `refusal` and the text under `refusal`. A normal text block has `type` of `text` and the text under `text`. A block may also be an object with those attributes. The helper must accept all three shapes. A string reply stays a string.

The script then reads `run.inputs`, `run.outputs`, `run.id`, and `run.name`. The new query returns only `id` unless `selects` lists the other fields. `run_type` must be uppercase `"CHAIN"`. `project_name` is gone; pass `project_ids` with the UUID from `client.read_project(project_name=...)`. `start_time` is now `min_start_time`, and omitting it silently means "last 24 hours", so the 30-day value must be passed. `limit` is gone; stop the async iterator after `limit * 3` runs. `page_size` is the per-request batch (default 100, max 1000).

Official mapping, from the LangSmith migration guide (do not invent a different one):

| Today | After |
| --- | --- |
| `client.list_runs(project_name=..., run_type="chain", start_time=since, limit=n)` | `async for run in client.runs.query(project_ids=[uuid], run_type="CHAIN", min_start_time=since, page_size=..., selects=[...])` |
| `client.create_feedback(run_id=..., key=..., score=...)` | same method, plus required `session_id` (project UUID) |
| `client.update_run(...)` in `tag_for_review` | unchanged |
| tracing via `LANGCHAIN_TRACING_V2` | unchanged |

`client.runs.query` is async. Drive it with `asyncio.run` from the script's synchronous `build_dataset`. Reference: https://docs.langchain.com/langsmith/smithdb-sdk-migration-query-runs and https://docs.langchain.com/langsmith/smithdb-sdk-migration-feedback

Library floors, checked on PyPI on 2026-11-18. Re-check them in step 1. Do not trust these numbers if PyPI has moved.

| Package | Floor this plan allows | Seen current on 2026-11-18 | Python |
| --- | --- | --- | --- |
| `langsmith` | `>=0.14.9` and `<0.16` | `0.15.0` (uploaded 2026-11-17). SmithDB methods exist from `0.10.15`; `langchain-core` 1.8 requires `>=0.14.9` | `>=3.11` |
| `langchain` | stays `>=1.0,<2.0`, then pin the resolved version | `1.5.1` | `>=3.11` |
| `langchain-core` | stays `>=1.0,<2.0`, then pin the resolved version | `1.8.3`, requires `langsmith>=0.14.9,<1` | `>=3.11` |
| `langgraph` | stays `>=1.0,<2.0`, then pin the resolved version | `1.2.22` | `>=3.10` |

GitHub Actions and `Dockerfile` already use Python 3.11. This machine also has `python3.11` on `PATH`. Use that interpreter for the install check so it matches the cron.

## Commands you will need

| Purpose | Command | Expected on success |
| --- | --- | --- |
| Python | `python3.11 --version` | prints `Python 3.11.` something |
| Logic tests, from repo root | `python3.11 tests/test_discovery_improvements.py` | process exits 0 and the script does not print `FAIL` |
| Logic tests | `python3.11 tests/test_value_improvements.py` | exits 0, no `FAIL` line |
| New test | `python3.11 tests/test_online_eval.py` | exits 0, no `FAIL` line |
| Import the inherited graph | `python3.11 -c "from src.workflows.workflow import create_workflow; g = create_workflow(); print(type(g).__name__)"` | prints a graph type name and exits 0 |

Run the test commands with `PYTHONPATH=.` if a bare run fails on `from src...`. The existing hunter step does not set `PYTHONPATH` for the logic tests; `tests/test_value_improvements.py` inserts the repo root on `sys.path` itself. Do the same in the new test.

There is no pytest config and no linter script. Do not add one.

## Suggested executor toolkit

- Read https://docs.langchain.com/langsmith/smithdb-sdk-migration before editing, then the query-runs and feedback pages linked above. If a parameter in this plan disagrees with those pages, STOP and report the disagreement. Do not invent a parameter.
- The local skill `.cursor/skills/langchain-dependencies/SKILL.md` says LangChain 1.0 is the LTS line and LangGraph is the orchestrator this project uses. Stay on that. Do not add `deepagents`.

## Scope

**In scope** (the only files you should modify):

- `requirements.txt`
- `src/core/online_eval.py`
- `src/llm.py` (`message_text` and `invoke_with_fallback` only)
- `src/agents/debate.py` (the three `.content` reads only)
- `src/agents/news_intelligence_agent.py` (the two `response.content` reads only)
- `scripts/build_golden_dataset.py`
- `scripts/evaluators.py` (the two `response.content` reads only)
- `tests/test_online_eval.py` (create)
- `.github/workflows/hunter.yml` (one added test line only)
- `plans/README.md` (status row only, when finished)

**Out of scope** (do NOT touch, even though they look related):

- `src/agent.py`, `src/whale_hunter.py`, `src/reeval_portfolio.py` — they already pass `run_id`. Session id is read inside `online_eval.py`.
- `invoke_structured` and the `RunnableConfig` tags in `src/llm.py`. Tracing stays as it is.
- `src/prompts/senior_broker.py`, `scripts/push_prompt_to_hub.py`, `scripts/run_evals.py` — Hub and experiments are not part of this removal.
- `src/workflows/**`, `src/backtesting/**`, `src/agents/data_collection_agent.py`, `src/agents/technical_analysis_agent.py`, `src/agents/portfolio_manager_agent.py` — keep them working by the import check. Do not port upstream files over them.
- `app.py`, `test_models.py`, `test_llm.py`, `check_model.py` — `app.py` reads the human Chainlit message, not a model reply.
- LangChain 1.5 `ModelRouterMiddleware`, the Decisions API, Deep Agents, annotation queues, and per-node timeouts. Do not add them.
- `requirements2.txt`, `Dockerfile`, `reeval.yml`, `LANGSMITH_GUIDE.md`, `README.md`.
- Any `.env` file. Never print or copy a key.
- Do not `git merge` or cherry-pick `ivebotunac/PrimoAgent`.
- Do not call LangSmith write endpoints (`create_feedback`, `create_example`, `create_dataset`, `update_run`) against the real API while testing. Mock the client.
- Do not run `scripts/build_golden_dataset.py` or `scripts/run_evals.py` against production.

## Git workflow

- Branch: `advisor/001-langsmith-library-upgrade`
- Three commits. Messages:
  - `feat(deps): pin LangSmith 0.15 and the LangChain stack it unblocks`
  - `fix(langsmith): send session id with verdict feedback and query runs via SmithDB`
  - `fix(llm): read LangChain content blocks as text`
- Do NOT push or open a PR unless the operator instructed it.
- The worktree already has untracked files (`.cursor/`, `docs/Sprint9_primogreedy.html`, `engine.py`, `index.html`, `langchain-skills/`, `server.ts`). Do not stage them.

## Steps

### Step 1: Confirm today's package versions, then install them

From the repo root, with a clean tree for the in-scope files:

```bash
python3.11 --version
python3.11 - <<'PY'
import json, urllib.request
for name in ("langsmith", "langchain", "langchain-core", "langgraph", "langchain-openai"):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=30) as r:
        info = json.load(r)["info"]
    print(f"{name} {info['version']} requires_python={info.get('requires_python')}")
PY
```

**Verify**: `langsmith` prints a version `>=0.14.9` and `<0.16`, and its `requires_python` allows 3.11. `langchain` and `langchain-core` print a 1.x version.

If `langsmith` latest is `0.16` or higher, STOP. This plan's query notes were written for the 0.15 API. If latest is `0.15.x` newer than `0.15.0`, use that `0.15.x`. If `0.15.0` is still latest, use `0.15.0`.

Replace only this line in `requirements.txt`:

```text
langsmith==0.7.9
```

with the chosen pin, for example `langsmith==0.15.0`. Leave the other lines as ranges for this first install.

Create a throwaway venv outside the repo and install:

```bash
python3.11 -m venv /tmp/pg-upgrade-001
/tmp/pg-upgrade-001/bin/pip install -r requirements.txt
/tmp/pg-upgrade-001/bin/python - <<'PY'
import langsmith, langchain, langchain_core, langgraph
from importlib.metadata import version
mods = ["langsmith", "langchain", "langchain-core", "langchain-openai", "langchain-community", "langchain-text-splitters", "langgraph"]
for m in mods:
    print(f"{m}=={version(m)}")
parts = tuple(int(x) for x in version("langsmith").split(".")[:3])
assert parts >= (0, 14, 9) and parts < (0, 16, 0), parts
PY
```

**Verify**: the command exits 0 and prints `langsmith==0.15` something. Pip must not print that it is walking backwards off `langchain-core` 1.8 to 1.6 because of langsmith. A normal install log is fine.

Then replace the open ranges for these packages with the exact versions that venv printed, using `==`:

- `langsmith`
- `langchain`
- `langchain-core`
- `langchain-openai`
- `langchain-community`
- `langchain-text-splitters`
- `langgraph`

Keep each existing upper bound's intent by pinning the version that already satisfied it. Example shape, using whatever the venv printed, not these sample numbers:

```text
langchain==1.5.1
langchain-community==0.4.2
langchain-core==1.8.3
langchain-text-splitters==1.1.3
langsmith==0.15.0
langchain-openai==1.8.2
langgraph==1.2.22
```

Re-run the venv install once after pinning. **Verify**: `/tmp/pg-upgrade-001/bin/pip install -r requirements.txt` exits 0 with no backtracking note about `langsmith`.

Do not pin `chainlit`, `yfinance`, or the rest of the file.

### Step 2: Prove the agent imports and the original workflow still build

Use the same venv, from the repo root:

```bash
cd /home/cisco/Documents/PrimoGreedy/primogreedy-main
/tmp/pg-upgrade-001/bin/python -c "from langgraph.graph import StateGraph, START, END, Command; from langgraph.types import RetryPolicy, Send; from langchain_openai import ChatOpenAI; print('imports-ok')"
PYTHONPATH=. /tmp/pg-upgrade-001/bin/python -c "from src.workflows.workflow import create_workflow; g = create_workflow(); print(type(g).__name__)"
PYTHONPATH=. /tmp/pg-upgrade-001/bin/python tests/test_value_improvements.py
PYTHONPATH=. /tmp/pg-upgrade-001/bin/python tests/test_discovery_improvements.py
```

**Verify**: both test scripts exit 0 and their output contains no line starting with `FAIL`. The import lines print `imports-ok` and a graph class name.

`create_workflow()` is the inherited PrimoAgent pipeline: four nodes (`data_collection`, `technical_analysis`, `news_intelligence`, `portfolio_manager`) in a straight line. If this import fails, the library bump is not safe. STOP. Do not patch the workflow to make the import pass.

### Step 3: Send `session_id` with verdict feedback

Edit only `src/core/online_eval.py`.

Add a helper next to `get_current_run_id` that returns `(run_id, session_id)` from `get_current_run_tree()`. `session_id` on that object is the project UUID. Return `(None, None)` when the tree or the import is missing. Keep `get_current_run_id` working for the existing callers; implement it with the helper or leave it as it is.

Inside `log_online_feedback`, after the existing `LANGCHAIN_API_KEY` / `LANGCHAIN_TRACING_V2` guard:

1. If `run_id` is missing, keep today's behavior: do not call `create_feedback`. The info logs for the local scores may still run. Do not require a run id to compute the scores.
2. Resolve `session_id` from the current run tree when `run_id` is present.
3. If the tree has no `session_id`, resolve it once per process with `client.read_project(project_name=os.getenv("LANGCHAIN_PROJECT") or "primogreedy")` and cache the UUID on a module-level variable. `read_project` is not a deprecated method.
4. If `session_id` is still missing, do not call `create_feedback`. Log a warning that names the ticker and says the project id was missing. Do not include the API key or the exception text if it might contain a request URL with a key.
5. Pass `session_id=session_id` into every `create_feedback` call, along with the existing `run_id`, `key`, `score`, `comment`, and `source_info`.
6. Change the `except` in `log_online_feedback` from `logger.debug` to `logger.warning`. The message stays a single sentence that feedback was skipped, plus the exception class name, not `str(exc)` if that string could contain the key. `tag_for_review` may stay on `logger.debug`. Do not change `update_run`.

Do not post feedback when tracing is off. The existing guard stays.

### Step 4: Move the golden-dataset query to SmithDB

Edit `scripts/build_golden_dataset.py`.

Add a function that returns the query keyword arguments and nothing else, so a test can check them without a network call. It must produce:

- `project_ids`: a list of one string UUID
- `run_type`: `"CHAIN"`
- `min_start_time`: the same 30-day `datetime` the script already computes (`datetime.utcnow() - timedelta(days=30)` is fine to keep; do not change the window)
- `page_size`: `min(limit * 3, 100)` unless `limit * 3` exceeds 100, in which case use `min(limit * 3, 1000)`
- `selects`: include `"ID"`, `"NAME"`, `"INPUTS"`, `"OUTPUTS"`. Add `"RUN_TYPE"` and `"START_TIME"` as well. Do not omit `INPUTS` or `OUTPUTS`; the loop below reads them.

Replace `client.list_runs(...)` with an async collector:

```python
async def _fetch_runs(client, project_name: str, since, fetch_cap: int) -> list:
    project = await client.aread_project(project_name=project_name)
    kwargs = query_kwargs(project_id=str(project.id), since=since, fetch_cap=fetch_cap)
    runs = []
    async for run in client.runs.query(**kwargs):
        runs.append(run)
        if len(runs) >= fetch_cap:
            break
    return runs
```

Call it with `asyncio.run(...)` from `build_dataset`. `fetch_cap` is the old `limit * 3`. Keep the rest of the bucketing and `create_example` logic. Do not run this function in tests.

If `aread_project` does not exist on the installed client and `read_project` does, use `read_project` from the sync `build_dataset` before entering the async query, and pass the UUID in. Check the installed client with:

```bash
/tmp/pg-upgrade-001/bin/python -c "import inspect; from langsmith import Client; print(hasattr(Client, 'aread_project')); print('runs' in dir(Client()))"
```

**Verify**: that command prints `True` and `True`. If `runs` is missing, STOP. The SDK is too old for this plan.

If `selects` rejects `"OUTPUTS"`, STOP and paste the error. Do not guess another field name.

### Step 5: Read model replies as text when content is a list of blocks

Add `message_text` to `src/llm.py`, next to `invoke_with_fallback`. It returns a `str` and does no network or logging.

- `None` returns `""`.
- A `str` returns that string unchanged.
- A `list` walks each item. A `str` item is kept. A `dict` contributes `text` if it is a non-empty string, otherwise `refusal` if that is a non-empty string. Any other object contributes `text` or `refusal` the same way via attributes. Join the parts with `""`.
- Anything else returns `str(content)`.

Use it in `invoke_with_fallback` in place of the `.strip()` line. Strip the helper result. If that is empty, keep the existing `RuntimeError`. Return the stripped string, not `response.content`. Leave the `RunnableConfig` tags as they are.

In `src/agents/debate.py`, import `message_text` from `src.llm`. In `_resilient_llm_call`, both the primary and fallback loops become `message_text(...invoke(...).content).strip()`, and the empty check stays. In `_verdict_from_plain_json`, assign `raw` from `message_text(...).strip()` before the fence stripping. Do not change model names, delays, or the JSON schema.

In `src/agents/news_intelligence_agent.py`, replace both `str(response.content).strip()` calls with `message_text(response.content).strip()`.

In `scripts/evaluators.py`, set `raw = message_text(response.content)` once per judge and use `raw` for `json.loads`, the regex, and the `[:200]` slice. Do that in both `catalyst_grounding` and `company_identity_score`. Import `message_text` from `src.llm`. `PYTHONPATH=.` is how the cron runs scripts; the import must work that way. If importing `src.llm` from this script pulls a heavy side effect, STOP and report it instead of copying the helper.

**Verify**:

```bash
PYTHONPATH=. /tmp/pg-upgrade-001/bin/python - <<'PY'
from src.llm import message_text
assert message_text("BUY") == "BUY"
assert message_text(None) == ""
assert message_text([{"type": "text", "text": "BUY"}]) == "BUY"
assert message_text([{"type": "refusal", "refusal": "no"}]) == "no"
assert message_text([]) == ""
print("message-text-ok")
PY
```

Expected: prints `message-text-ok` and exits 0.

### Step 6: Add a network-free test and run it in the hunter workflow

Create `tests/test_online_eval.py` using the `check(name, cond, detail="")` pattern and the `sys.path` insert at the top of `tests/test_value_improvements.py`. The file must not read `.env` and must not call the network. Set `LANGCHAIN_API_KEY` and `LANGCHAIN_TRACING_V2` inside the test process only.

Cases:

1. With a fake run tree whose `id` is `run-1` and `session_id` is `sess-1`, and a fake `Client` whose `create_feedback` records kwargs, `log_online_feedback("### FINAL VERDICT\nBUY", "AAPL", run_id="run-1")` calls `create_feedback` twice (format and verdict) and every call includes `session_id="sess-1"` and `run_id="run-1"`.
2. With `run_id=None`, `create_feedback` is not called.
3. When `create_feedback` raises `RuntimeError("nope")`, `log_online_feedback` returns normally and does not raise.
4. The pure query-kwargs function from `scripts/build_golden_dataset.py` returns `run_type=="CHAIN"`, `project_ids==["abc"]`, `selects` containing `INPUTS` and `OUTPUTS`, and a `min_start_time` about 30 days before now (allow a 2-minute skew). Importing that module must not require a real API key. If today's module calls `load_dotenv()` and exits when the key is missing at import time, move the key check back inside `build_dataset` so import is safe. The key check already lives in `_get_client()`, which is only called from `build_dataset`. Keep it that way.
5. `message_text("BUY")` is `"BUY"`. `message_text(None)` and `message_text([])` are `""`. `message_text([{"type": "text", "text": "BUY"}])` is `"BUY"`. `message_text([{"type": "refusal", "refusal": "no"}])` is `"no"`. A mixed list with a text block and a refusal block concatenates both strings. None of these call a model.

Monkeypatch `langsmith.Client` and `get_current_run_tree` inside the test. Do not install extra test packages.

In `.github/workflows/hunter.yml`, add one line under the existing logic-test step, after `python tests/test_value_improvements.py`:

```yaml
          python tests/test_online_eval.py
```

**Verify**:

```bash
PYTHONPATH=. /tmp/pg-upgrade-001/bin/python tests/test_online_eval.py
```

exits 0 and prints no `FAIL` line. Then re-run `tests/test_value_improvements.py` and `tests/test_discovery_improvements.py` the same way. Both exit 0.

Search the repo for the old call:

```bash
rg -n "list_runs\(" --glob '!**/langchain-skills/**' --glob '!**/.git/**' --glob '!**/primo_env/**' --glob '!**/test_env/**'
```

**Verify**: the only matches are comments or this plan file. `scripts/build_golden_dataset.py` has no `list_runs(` call.

### Step 7: Commit only the in-scope files

```bash
git status --short
git diff -- requirements.txt src/core/online_eval.py src/llm.py src/agents/debate.py src/agents/news_intelligence_agent.py scripts/build_golden_dataset.py scripts/evaluators.py tests/test_online_eval.py .github/workflows/hunter.yml
```

**Verify**: `git status` shows no staged or unstaged changes outside that list and `plans/README.md`. Untracked files that were already untracked may still show as untracked. Do not add them.

Commit the three logical units described in Git workflow. Update the status row in `plans/README.md` to `DONE`.

## Test plan

- New file `tests/test_online_eval.py`, modeled on `tests/test_value_improvements.py`.
- Cases listed in step 6: session id is passed, missing run id skips the API, a client error does not raise, query kwargs request chain runs plus inputs and outputs over 30 days, and `message_text` turns a string, a text block, and a refusal block into text.
- Existing `tests/test_value_improvements.py` and `tests/test_discovery_improvements.py` still pass on the new venv. Those cover the hunter's UK currency math and discovery logic, which import LangGraph.
- `create_workflow()` imports. That is the check that the original PrimoAgent pipeline still constructs.
- No live LangSmith call is part of the test plan.

## Done criteria

ALL must hold:

- [ ] `requirements.txt` pins `langsmith` to a `0.15.x` version (`>=0.14.9` and `<0.16`) and pins `langchain`, `langchain-core`, `langchain-openai`, `langchain-community`, `langchain-text-splitters`, and `langgraph` to the versions the step 1 venv resolved
- [ ] `/tmp/pg-upgrade-001/bin/pip install -r requirements.txt` exits 0
- [ ] `PYTHONPATH=. /tmp/pg-upgrade-001/bin/python -c "from src.workflows.workflow import create_workflow; create_workflow()"` exits 0
- [ ] `tests/test_value_improvements.py`, `tests/test_discovery_improvements.py`, and `tests/test_online_eval.py` exit 0 on that interpreter and print no `FAIL` line
- [ ] `rg -n "list_runs\(" scripts src tests` prints nothing
- [ ] `create_feedback` in `src/core/online_eval.py` is called with a `session_id` argument
- [ ] `log_online_feedback`'s handler uses `logger.warning`, not `logger.debug`
- [ ] `hunter.yml` runs `python tests/test_online_eval.py`
- [ ] `src/llm.py` defines `message_text`, and `invoke_with_fallback` returns its stripped result
- [ ] `src/agents/debate.py`, `src/agents/news_intelligence_agent.py`, and `scripts/evaluators.py` do not call `.strip()` or `json.loads` on `.content` directly. They go through `message_text`
- [ ] `git status` shows no modifications outside the in-scope files
- [ ] `plans/README.md` status row for 001 is `DONE`

## STOP conditions

Stop and report back (do not improvise) if:

- The excerpts in "Current state" do not match the files (drift since `cc5a7db`).
- `python3.11` is missing, or its version is below 3.11.
- PyPI's latest `langsmith` is outside `0.14.9` inclusive to `0.16` exclusive.
- The install still backtracks to `langchain-core` 1.6.x, or pip reports a resolution conflict.
- `Client` has no `runs` attribute after the install.
- `create_workflow()` or either existing logic test fails on the new venv.
- `runs.query` rejects `selects` value `"OUTPUTS"` or `"INPUTS"`.
- Making the golden-dataset module importable requires deleting `load_dotenv()` behavior that `build_dataset` needs. Adjust only the import-time key check, as step 6 says. If the key check cannot be kept inside `_get_client`, STOP.
- A fix seems to require editing `src/whale_hunter.py`, `src/agent.py`, `src/workflows/`, or `requirements2.txt`. `src/agents/news_intelligence_agent.py` is in scope only for the two `message_text` swaps.
- `message_text` would need a new block shape that is not a string, a dict, or an object with `text` or `refusal`. STOP and report the shape.
- A step's verification fails twice after a reasonable fix attempt.

## Maintenance notes

- The cron still writes traces through the same `RunnableConfig` tags in `src/llm.py`. Reviewers should check that `hunter.yml` still sets `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, and `LANGCHAIN_PROJECT`, and that `invoke_with_fallback` returns a `str`.
- A future content-block type with no `text` and no `refusal` will come back as an empty string and the fallback will try the next model. That is the intended failure. Add a field to `message_text` only when a real reply uses one.
- Reviewers should check that `create_feedback` cannot be reached without `session_id`, and that the warning path cannot print the API key.
- The golden-dataset script now stops after `limit * 3` runs. SmithDB will paginate the whole window if that break is removed.
- `min_start_time` must stay explicit. Dropping it shrinks the window to 24 hours.
- When a future `langsmith` 0.16 or `langchain` 2.0 is considered, re-read the SmithDB guide before bumping. Do not widen these pins in a drive-by.
- `tag_for_review` still uses `update_run`. That method was not on the 2027-01-31 removal list when this plan was written. If a later LangSmith notice names it, migrate it in a separate change.
- `scripts/run_evals.py` still calls `evaluate()`. It is manual. Run it only when someone intends to create an experiment.
- Upstream `ivebotunac/PrimoAgent` should not be merged to "catch up". Its useful remainder is already in `src/workflows/` and `src/backtesting/`. New hunter behavior belongs in this fork.
- Thread queries, annotation-queue items, and shared-run APIs are available after this upgrade and are unused. Add one only when a concrete review workflow needs it.
