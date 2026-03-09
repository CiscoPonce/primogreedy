"""Run PrimoGreedy evaluators against the golden dataset.

Usage:
    python scripts/run_evals.py [--dataset primogreedy-golden-v1] [--experiment sprint9]

Runs all evaluators from ``scripts/evaluators.py`` against the specified
LangSmith dataset and posts results to the LangSmith Experiments dashboard.
"""

import argparse
import os
import sys

from dotenv import load_dotenv

load_dotenv()


def _passthrough_predictor(inputs: dict) -> dict:
    """Identity function — we evaluate stored outputs, not re-run the pipeline."""
    return inputs


def run_evaluation(dataset_name: str = "primogreedy-golden-v1", experiment_prefix: str = "sprint9"):
    api_key = os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        print("ERROR: Set LANGCHAIN_API_KEY or LANGSMITH_API_KEY env var.")
        sys.exit(1)

    try:
        from langsmith import Client, evaluate
    except ImportError:
        print("ERROR: langsmith package not installed.  pip install langsmith")
        sys.exit(1)

    from scripts.evaluators import ALL_EVALUATORS

    client = Client(api_key=api_key)

    try:
        client.read_dataset(dataset_name=dataset_name)
    except Exception:
        print(f"ERROR: Dataset '{dataset_name}' not found.")
        print("Run 'python scripts/build_golden_dataset.py' first.")
        sys.exit(1)

    print(f"Running {len(ALL_EVALUATORS)} evaluators against '{dataset_name}'...")
    print(f"Experiment prefix: {experiment_prefix}")
    print()

    results = evaluate(
        _passthrough_predictor,
        data=dataset_name,
        evaluators=ALL_EVALUATORS,
        experiment_prefix=experiment_prefix,
        client=client,
    )

    print("\n--- Evaluation Complete ---")
    print(f"Results posted to LangSmith under experiment prefix: {experiment_prefix}")
    print("View detailed results at: https://smith.langchain.com")

    try:
        for result in results:
            example_id = result.get("example_id", "?")
            scores = result.get("evaluation_results", {})
            print(f"\n  Example {example_id}:")
            if isinstance(scores, dict):
                for key, val in scores.items():
                    score = val.get("score", "?") if isinstance(val, dict) else val
                    print(f"    {key}: {score}")
            elif isinstance(scores, list):
                for s in scores:
                    key = s.get("key", "?") if isinstance(s, dict) else "?"
                    score = s.get("score", "?") if isinstance(s, dict) else s
                    print(f"    {key}: {score}")
    except Exception:
        print("  (Results streamed to LangSmith — check the dashboard for details)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run PrimoGreedy evaluations")
    parser.add_argument("--dataset", default="primogreedy-golden-v1")
    parser.add_argument("--experiment", default="sprint9")
    args = parser.parse_args()
    run_evaluation(dataset_name=args.dataset, experiment_prefix=args.experiment)
