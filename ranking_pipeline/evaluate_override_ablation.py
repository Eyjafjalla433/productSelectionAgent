"""Evaluate the override-aware branch against the saved locked-exact baseline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluator import local_evaluator

from ranking_pipeline.override_aware_agent import OverrideAwareAgent


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
RETRIEVAL_ROOT = REPOSITORY_ROOT / "retrieval-and-reranking"
RESULTS_ROOT = REPOSITORY_ROOT / "ranking_pipeline" / "results"
BASELINE_RESULT = RESULTS_ROOT / "locked-exact.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=RETRIEVAL_ROOT / "data" / "catalog.jsonl")
    parser.add_argument("--dataset", type=Path, default=RETRIEVAL_ROOT / "data" / "public_set.jsonl")
    parser.add_argument("--output", type=Path, default=RESULTS_ROOT / "override-aware-exact.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    samples = local_evaluator.load_jsonl(args.dataset)
    catalog_ids, categories, products = local_evaluator.catalog_index(args.catalog)
    agent = OverrideAwareAgent(args.catalog, retrieval_mode="exact")
    result = local_evaluator.evaluate(agent, samples, catalog_ids, categories, products)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    scenario = result.get("scenario_metrics") or {}
    baseline_data = json.loads(BASELINE_RESULT.read_text(encoding="utf-8"))
    baseline_scenario = baseline_data.get("scenario_metrics") or {}
    hard = scenario.get("intent_override") or {}
    hard_baseline = baseline_scenario.get("intent_override") or {}

    print(json.dumps({key: value for key, value in result.items() if key != "sessions"}, indent=2))
    print(
        json.dumps(
            {
                "intent_override_old": hard_baseline,
                "intent_override_new": hard,
                "delta_hit@10": round(float(hard.get("hit_rate_at_10", 0)) - float(hard_baseline.get("hit_rate_at_10", 0)), 6),
                "delta_mrr": round(float(hard.get("mrr", 0)) - float(hard_baseline.get("mrr", 0)), 6),
                "delta_mttc": round(float(hard.get("mttc", 0)) - float(hard_baseline.get("mttc", 0)), 6),
                "output": str(args.output),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
