"""Inline (online) evaluators — run after each analyst verdict during cron.

Only runs the *cheap* evaluators (no LLM calls):
  - format_score: structural checks (headers, duplicates, Kelly section)
  - verdict_validity_score: valid verdict keyword present

Results are logged as LangSmith feedback on the current run.
Falls back silently if LangSmith is not configured.
"""

import os
import re

from src.core.logger import get_logger

logger = get_logger(__name__)

VALID_VERDICTS = {"STRONG BUY", "BUY", "WATCH", "AVOID"}

REQUIRED_HEADERS = [
    "### THE QUANTITATIVE BASE",
    "### THE LYNCH PITCH",
    "### THE MUNGER INVERT",
    "### FINAL VERDICT",
]


def _format_score(verdict_text: str) -> dict:
    """Check structural correctness of the verdict report."""
    if not verdict_text or "REJECTED" in verdict_text.upper():
        return {"key": "format_score", "score": 1.0, "comment": "Rejected/empty, N/A"}

    issues = []
    total_checks = 0

    for header in REQUIRED_HEADERS:
        total_checks += 1
        count = verdict_text.count(header)
        if count == 0:
            issues.append(f"Missing: {header}")
        elif count > 1:
            issues.append(f"Duplicated ({count}x): {header}")

    upper = verdict_text.upper()
    is_buy = "STRONG BUY" in upper or ("BUY" in upper and "AVOID" not in upper)

    if is_buy:
        total_checks += 1
        if "POSITION SIZING" not in verdict_text and "Kelly" not in verdict_text:
            issues.append("Missing Kelly section for BUY verdict")

    passed = total_checks - len(issues)
    score = passed / total_checks if total_checks > 0 else 1.0

    return {
        "key": "format_score",
        "score": score,
        "comment": "; ".join(issues) if issues else "All format checks passed",
    }


def _verdict_validity_score(verdict_text: str) -> dict:
    """Check that the final verdict is one of the 4 valid values."""
    if not verdict_text or "REJECTED" in verdict_text.upper():
        return {"key": "verdict_validity", "score": 1.0, "comment": "Rejected, N/A"}

    upper = verdict_text.upper()
    found = None
    if "STRONG BUY" in upper:
        found = "STRONG BUY"
    elif "BUY" in upper:
        found = "BUY"
    elif "WATCH" in upper:
        found = "WATCH"
    elif "AVOID" in upper:
        found = "AVOID"

    if found and found in VALID_VERDICTS:
        return {"key": "verdict_validity", "score": 1.0, "comment": f"Valid: {found}"}
    return {"key": "verdict_validity", "score": 0.0, "comment": "Invalid/missing verdict"}


def log_online_feedback(
    verdict_text: str,
    ticker: str,
    *,
    run_id: str | None = None,
    is_fallback: bool = False,
) -> None:
    """Run cheap evaluators and post results as LangSmith feedback.

    Requires LANGCHAIN_API_KEY and LANGCHAIN_TRACING_V2=true in env.
    Fails silently if LangSmith is unavailable.
    """
    api_key = os.getenv("LANGCHAIN_API_KEY", "")
    tracing = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"

    if not api_key or not tracing:
        return

    evals = [
        _format_score(verdict_text),
        _verdict_validity_score(verdict_text),
    ]

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


def tag_for_review(
    verdict_text: str,
    ticker: str,
    *,
    run_id: str | None = None,
    is_fallback: bool = False,
) -> None:
    """Tag LangSmith runs that need human review.

    Criteria:
      - WATCH or AVOID verdicts (edge cases worth reviewing)
      - Fallback-path verdicts (structured output failed)
    """
    api_key = os.getenv("LANGCHAIN_API_KEY", "")
    tracing = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"

    if not api_key or not tracing or not run_id:
        return

    upper = (verdict_text or "").upper()
    needs_review = is_fallback or "WATCH" in upper or "AVOID" in upper

    if not needs_review:
        return

    reasons = []
    if is_fallback:
        reasons.append("fallback_path")
    if "WATCH" in upper:
        reasons.append("WATCH_verdict")
    if "AVOID" in upper:
        reasons.append("AVOID_verdict")

    try:
        from langsmith import Client

        client = Client()
        client.update_run(
            run_id,
            extra={
                "metadata": {
                    "needs_review": True,
                    "review_reasons": reasons,
                    "ticker": ticker,
                }
            },
            tags=["needs_review"] + reasons,
        )
        logger.info(
            "Tagged run %s for review: %s (%s)",
            run_id[:8] if run_id else "?", ticker, ", ".join(reasons),
        )
    except Exception as exc:
        logger.debug("LangSmith annotation skipped: %s", exc)


def get_current_run_id() -> str | None:
    """Attempt to retrieve the current LangSmith run ID from callback context."""
    try:
        from langsmith import get_current_run_tree
        rt = get_current_run_tree()
        return str(rt.id) if rt else None
    except Exception:
        return None
