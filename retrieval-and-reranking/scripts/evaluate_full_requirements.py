"""Run the Agent on a 100-buying/100-browsing public projection."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any, Sequence

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from techjam_agent.agent import Agent


ROOT = Path(__file__).resolve().parents[1]


def project_to_buying_browsing(samples: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [deepcopy(sample) for sample in samples]
    counts = Counter(
        str(sample["scenario_type"])
        for sample in result
        if sample["scenario_type"] in {"buying", "browsing"}
    )
    for sample in sorted(result, key=lambda row: str(row["sample_id"])):
        original = str(sample["scenario_type"])
        if original in {"buying", "browsing"}:
            continue
        replacement = min(("buying", "browsing"), key=lambda name: (counts[name], name))
        sample["original_scenario_type"] = original
        sample["scenario_type"] = replacement
        counts[replacement] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.jsonl")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/public_set.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "results/full_requirements.json")
    args = parser.parse_args()
    samples = project_to_buying_browsing(load_jsonl(args.dataset))
    catalog_ids, categories, products = catalog_index(args.catalog)
    result = evaluate(Agent(args.catalog), samples, catalog_ids, categories, products)
    result["boundary"] = (
        "Counterfactual 100-buying/100-browsing diagnostic; not the original four-scenario score."
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "sessions"}, indent=2))


if __name__ == "__main__":
    main()
