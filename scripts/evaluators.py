"""Custom LangSmith evaluators for PrimoGreedy analyst pipeline.

Evaluator categories:
  1. Hallucination catchers (LLM-as-a-Judge) — catalyst_grounding_score, company_identity_score
  2. Format verifiers (exact-match) — format_score, verdict_validity_score
  3. Math verifier — kelly_math_score

Each evaluator conforms to the ``langsmith.evaluate()`` protocol:
    def evaluator(run, example) -> EvaluationResult | dict
"""

import os
import re

from dotenv import load_dotenv

load_dotenv()

VALID_VERDICTS = {"STRONG BUY", "BUY", "WATCH", "AVOID"}

REQUIRED_HEADERS = [
    "### THE QUANTITATIVE BASE",
    "### THE LYNCH PITCH",
    "### THE MUNGER INVERT",
    "### FINAL VERDICT",
]


# ---------------------------------------------------------------------------
# 1. Hallucination catchers (LLM-as-a-Judge)
# ---------------------------------------------------------------------------

def catalyst_grounding_score(run, example) -> dict:
    """Score whether the Lynch Pitch catalyst is grounded in provided context.

    Uses an LLM-as-a-Judge prompt to compare the analyst's catalyst claim
    against the data that was actually in the prompt.  Returns 0 (fabricated)
    to 1 (fully grounded).
    """
    inputs = run.inputs or {}
    outputs = run.outputs or {}

    context_parts = []
    if inputs.get("financial_data"):
        context_parts.append(str(inputs["financial_data"])[:3000])
    if inputs.get("sec_context"):
        context_parts.append(str(inputs["sec_context"])[:2000])
    if inputs.get("deep_fundamentals"):
        context_parts.append(str(inputs["deep_fundamentals"])[:2000])
    context = "\n".join(context_parts)

    verdict_text = str(outputs.get("final_verdict", ""))

    lynch_match = re.search(
        r"###\s*THE LYNCH PITCH.*?\n(.*?)(?=###|\Z)",
        verdict_text,
        re.DOTALL,
    )
    lynch_pitch = lynch_match.group(1).strip() if lynch_match else verdict_text[:500]

    if not context or not lynch_pitch:
        return {"key": "catalyst_grounding", "score": 0.5, "comment": "Insufficient data"}

    try:
        from langchain_openai import ChatOpenAI

        judge_llm = ChatOpenAI(
            model=os.getenv("EVAL_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free"),
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
            max_tokens=256,
        )

        judge_prompt = (
            "You are a fact-checking judge. Given the CONTEXT the analyst received "
            "and the CATALYST CLAIM it made, determine whether the claim has "
            "grounding in the context.\n\n"
            "Score on a scale from 0.0 (completely fabricated, no evidence in context) "
            "to 1.0 (fully grounded in the data provided).\n\n"
            "Respond with ONLY a JSON object: {\"score\": <float>, \"reason\": \"<short reason>\"}\n\n"
            f"CONTEXT:\n{context[:4000]}\n\n"
            f"CATALYST CLAIM:\n{lynch_pitch[:1000]}"
        )

        response = judge_llm.invoke(judge_prompt)
        import json
        try:
            result = json.loads(response.content)
            score = float(result.get("score", 0.5))
            reason = result.get("reason", "")
        except (json.JSONDecodeError, ValueError):
            score_match = re.search(r"(\d+\.?\d*)", response.content)
            score = float(score_match.group(1)) if score_match else 0.5
            reason = response.content[:200]

        return {"key": "catalyst_grounding", "score": max(0, min(1, score)), "comment": reason}

    except Exception as exc:
        return {"key": "catalyst_grounding", "score": 0.5, "comment": f"Judge error: {exc}"}


def company_identity_score(run, example) -> dict:
    """Check whether the LLM correctly identifies the company's business.

    Catches hallucinations like "High Arctic = Arctic drilling" by comparing
    the analyst's description against the actual sector/business from
    financial_data.
    """
    inputs = run.inputs or {}
    outputs = run.outputs or {}

    financial_data = str(inputs.get("financial_data", ""))
    verdict_text = str(outputs.get("final_verdict", ""))

    if not financial_data or not verdict_text:
        return {"key": "company_identity", "score": 0.5, "comment": "Insufficient data"}

    try:
        from langchain_openai import ChatOpenAI
        import json

        judge_llm = ChatOpenAI(
            model=os.getenv("EVAL_MODEL", "nvidia/nemotron-3-nano-30b-a3b:free"),
            api_key=os.getenv("OPENROUTER_API_KEY"),
            base_url="https://openrouter.ai/api/v1",
            temperature=0,
            max_tokens=256,
        )

        judge_prompt = (
            "You are a fact-checking judge. Compare the FINANCIAL DATA (ground truth) "
            "with the ANALYST REPORT to check if the analyst correctly identifies "
            "what the company actually does.\n\n"
            "Score 0.0 if the analyst describes a completely different business, "
            "0.5 if partially correct, 1.0 if accurate.\n\n"
            "Respond with ONLY: {\"score\": <float>, \"reason\": \"<short reason>\"}\n\n"
            f"FINANCIAL DATA:\n{financial_data[:3000]}\n\n"
            f"ANALYST REPORT:\n{verdict_text[:3000]}"
        )

        response = judge_llm.invoke(judge_prompt)
        try:
            result = json.loads(response.content)
            score = float(result.get("score", 0.5))
            reason = result.get("reason", "")
        except (json.JSONDecodeError, ValueError):
            score_match = re.search(r"(\d+\.?\d*)", response.content)
            score = float(score_match.group(1)) if score_match else 0.5
            reason = response.content[:200]

        return {"key": "company_identity", "score": max(0, min(1, score)), "comment": reason}

    except Exception as exc:
        return {"key": "company_identity", "score": 0.5, "comment": f"Judge error: {exc}"}


# ---------------------------------------------------------------------------
# 2. Format verifiers (exact-match, no LLM)
# ---------------------------------------------------------------------------

def format_score(run, example) -> dict:
    """Check structural correctness of the verdict report.

    Validates:
      - All 4 required headers are present
      - No duplicate headers (the double-header bug)
      - Kelly section present for BUY/STRONG BUY verdicts
    """
    outputs = run.outputs or {}
    verdict_text = str(outputs.get("final_verdict", ""))

    if not verdict_text or "REJECTED" in verdict_text.upper():
        return {"key": "format", "score": 1.0, "comment": "Rejected/empty, N/A"}

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
        "key": "format",
        "score": score,
        "comment": "; ".join(issues) if issues else "All format checks passed",
    }


def verdict_validity_score(run, example) -> dict:
    """Check that the final verdict is one of the 4 valid values."""
    outputs = run.outputs or {}
    verdict_text = str(outputs.get("final_verdict", ""))

    if not verdict_text or "REJECTED" in verdict_text.upper():
        return {"key": "verdict_validity", "score": 1.0, "comment": "Rejected, N/A"}

    found_verdict = None
    upper = verdict_text.upper()

    if "STRONG BUY" in upper:
        found_verdict = "STRONG BUY"
    elif "BUY" in upper:
        found_verdict = "BUY"
    elif "WATCH" in upper:
        found_verdict = "WATCH"
    elif "AVOID" in upper:
        found_verdict = "AVOID"

    if found_verdict and found_verdict in VALID_VERDICTS:
        return {"key": "verdict_validity", "score": 1.0, "comment": f"Valid: {found_verdict}"}

    return {"key": "verdict_validity", "score": 0.0, "comment": f"Invalid/missing verdict"}


# ---------------------------------------------------------------------------
# 3. Math verifier
# ---------------------------------------------------------------------------

def kelly_math_score(run, example) -> dict:
    """Verify Kelly position sizing math is within valid bounds.

    Checks that reported allocation is between 1% and 25% for BUY/STRONG BUY.
    """
    outputs = run.outputs or {}
    verdict_text = str(outputs.get("final_verdict", ""))
    upper = verdict_text.upper()

    is_buy = "STRONG BUY" in upper or ("BUY" in upper and "AVOID" not in upper)
    if not is_buy:
        return {"key": "kelly_math", "score": 1.0, "comment": "Non-buy, N/A"}

    match = re.search(r"allocation:\s*([\d.]+)%", verdict_text)
    if not match:
        return {"key": "kelly_math", "score": 0.5, "comment": "No allocation found in BUY verdict"}

    pct = float(match.group(1))
    if 1.0 <= pct <= 25.0:
        return {"key": "kelly_math", "score": 1.0, "comment": f"{pct}% within [1%, 25%]"}

    return {"key": "kelly_math", "score": 0.0, "comment": f"{pct}% outside valid range [1%, 25%]"}


ALL_EVALUATORS = [
    catalyst_grounding_score,
    company_identity_score,
    format_score,
    verdict_validity_score,
    kelly_math_score,
]
