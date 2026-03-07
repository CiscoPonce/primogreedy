"""Build the PrimoGreedy golden evaluation dataset from LangSmith traces.

Usage:
    python scripts/build_golden_dataset.py [--limit 50] [--project primogreedy]

Creates (or updates) a LangSmith Dataset named ``primogreedy-golden-v1``
with representative runs from the project, including a mix of BUY, AVOID,
WATCH, and failure cases.  After running, manually annotate expected verdicts
in the LangSmith UI.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()


def _get_client():
    from langsmith import Client

    api_key = os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        print("ERROR: Set LANGCHAIN_API_KEY or LANGSMITH_API_KEY env var.")
        sys.exit(1)
    return Client(api_key=api_key)


def _extract_verdict_type(output_text: str) -> str:
    """Classify the run's verdict from its output text."""
    upper = (output_text or "").upper()
    if "STRONG BUY" in upper:
        return "STRONG BUY"
    if "BUY" in upper:
        return "BUY"
    if "WATCH" in upper:
        return "WATCH"
    if "AVOID" in upper:
        return "AVOID"
    if "REJECTED" in upper or "FAIL" in upper:
        return "REJECTED"
    return "UNKNOWN"


def build_dataset(project_name: str = "primogreedy", limit: int = 50):
    client = _get_client()

    dataset_name = "primogreedy-golden-v1"

    try:
        dataset = client.read_dataset(dataset_name=dataset_name)
        print(f"Found existing dataset: {dataset_name} (id={dataset.id})")
    except Exception:
        dataset = client.create_dataset(
            dataset_name=dataset_name,
            description=(
                "Golden evaluation dataset for PrimoGreedy analyst pipeline. "
                "Contains representative runs across verdict types."
            ),
        )
        print(f"Created new dataset: {dataset_name} (id={dataset.id})")

    since = datetime.utcnow() - timedelta(days=30)
    runs = list(
        client.list_runs(
            project_name=project_name,
            run_type="chain",
            start_time=since,
            limit=limit * 3,
        )
    )

    if not runs:
        print(f"No runs found in project '{project_name}' in the last 30 days.")
        return

    print(f"Fetched {len(runs)} raw runs, classifying...")

    buckets: dict[str, list] = {
        "STRONG BUY": [],
        "BUY": [],
        "WATCH": [],
        "AVOID": [],
        "REJECTED": [],
        "UNKNOWN": [],
    }

    for run in runs:
        output_text = ""
        if run.outputs:
            output_text = str(run.outputs.get("final_verdict", ""))
            if not output_text:
                output_text = str(run.outputs)
        verdict_type = _extract_verdict_type(output_text)
        buckets[verdict_type].append(run)

    for vt, vt_runs in buckets.items():
        print(f"  {vt}: {len(vt_runs)} runs")

    selected = []
    per_bucket = max(1, limit // len(buckets))

    for vt in ["STRONG BUY", "BUY", "WATCH", "AVOID", "REJECTED", "UNKNOWN"]:
        bucket_runs = buckets[vt][:per_bucket]
        selected.extend(bucket_runs)

    remaining = limit - len(selected)
    if remaining > 0:
        all_remaining = [r for r in runs if r not in selected]
        selected.extend(all_remaining[:remaining])

    selected = selected[:limit]
    print(f"\nSelected {len(selected)} runs for the golden dataset.")

    added = 0
    for run in selected:
        inputs = {}
        if run.inputs:
            inputs = {
                "ticker": run.inputs.get("ticker", ""),
                "region": run.inputs.get("region", ""),
                "financial_data": str(run.inputs.get("financial_data", ""))[:2000],
            }

        output_text = ""
        if run.outputs:
            output_text = str(run.outputs.get("final_verdict", ""))
            if not output_text:
                output_text = str(run.outputs)

        verdict_type = _extract_verdict_type(output_text)

        try:
            client.create_example(
                inputs=inputs,
                outputs={
                    "final_verdict": output_text[:5000],
                    "verdict_type": verdict_type,
                },
                metadata={
                    "run_id": str(run.id),
                    "run_name": run.name or "",
                    "needs_annotation": True,
                },
                dataset_id=dataset.id,
            )
            added += 1
        except Exception as exc:
            print(f"  Skipped run {run.id}: {exc}")

    print(f"\nAdded {added} examples to dataset '{dataset_name}'.")
    print("Next step: Open LangSmith UI and annotate expected verdicts for each example.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build PrimoGreedy golden dataset")
    parser.add_argument("--limit", type=int, default=50, help="Max examples")
    parser.add_argument("--project", default="primogreedy", help="LangSmith project name")
    args = parser.parse_args()
    build_dataset(project_name=args.project, limit=args.limit)
