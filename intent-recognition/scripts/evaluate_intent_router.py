"""Measure Intent Router behavior on evaluator-materialized public messages."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluator.local_evaluator import (
    catalog_index,
    classify_constraint,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
)
from intent_router import IntentRouter, load_catalog_brands, load_catalog_categories


def has_expected_slot(result: object, attribute: str) -> bool:
    slots = result.slots
    if attribute == "budget":
        return any(name in slots for name in ("budget_min", "budget_max", "budget_target"))
    return bool(slots.get(attribute))


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate Intent Router on public evaluator messages")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="intent_router_results.json")
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    _, categories, products = catalog_index(args.catalog)
    router = IntentRouter(
        known_brands=load_catalog_brands(args.catalog),
        known_categories=load_catalog_categories(args.catalog),
    )
    constraint_total = 0
    constraint_covered = 0
    override_total = 0
    override_detected = 0
    per_scenario: dict[str, dict[str, int]] = {}
    intent_distribution = {"buying": 0, "browsing": 0, "undetermined": 0}

    for sample in samples:
        card, behavior = materialize_hidden_fields(sample, products)
        effective = {**sample, "intent_card": card, "behavior": behavior}
        category = " ".join(categories[str(sample["ground_truth"]["parent_asin"])][-2:])
        message = initial_message(effective, category, set())
        result = router.understand(message)
        scenario = str(sample["scenario_type"])
        metrics = per_scenario.setdefault(
            scenario, {"count": 0, "buying": 0, "browsing": 0, "undetermined": 0}
        )
        metrics["count"] += 1
        intent_name = result.intent_type or "undetermined"
        metrics[intent_name] += 1
        intent_distribution[intent_name] += 1

        if scenario == "buying" and card["hard_constraints"]:
            attribute = classify_constraint(str(card["hard_constraints"][0]))
            constraint_total += 1
            constraint_covered += int(has_expected_slot(result, attribute))

        if scenario == "intent_override":
            override_total += 1
            override_message = str(behavior["override"]["message"])
            override_detected += int(router.understand(override_message).override_detected)

    output = {
        "intent_distribution": intent_distribution,
        "initial_buying_constraint_coverage": round(constraint_covered / constraint_total, 4)
        if constraint_total else 0.0,
        "constraint_sample_count": constraint_total,
        "override_detection_recall": round(override_detected / override_total, 4) if override_total else 0.0,
        "override_sample_count": override_total,
        "scenario_counts": per_scenario,
    }
    Path(args.output).write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
