"""Validate locked Top-50 coverage and Top-10 ranking on public full cards."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluator.local_evaluator import (
    catalog_index,
    coarse_category,
    load_jsonl,
    materialize_hidden_fields,
)
from techjam_agent.contracts import Requirements
from techjam_agent.ranking import LockedWeightedRrfTop10Reranker
from techjam_agent.retrieval import ExactDenseTop50CandidateGenerator, LiteTop50CandidateGenerator


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.jsonl")
    parser.add_argument("--dataset", type=Path, default=ROOT / "data/public_set.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "results/pipeline_validation.json")
    parser.add_argument("--expect-top50", type=int, default=199)
    parser.add_argument("--expect-top10", type=int, default=198)
    parser.add_argument("--mode", choices=("exact", "lite"), default="exact")
    args = parser.parse_args()

    _, categories, products = catalog_index(args.catalog)
    samples = load_jsonl(args.dataset)
    generator = (
        ExactDenseTop50CandidateGenerator(args.catalog)
        if args.mode == "exact"
        else LiteTop50CandidateGenerator(args.catalog)
    )
    reranker = LockedWeightedRrfTop10Reranker()
    rows = []
    for sample in samples:
        target = str(sample["ground_truth"]["parent_asin"])
        card, behavior = materialize_hidden_fields(sample, products)
        excluded_soft: set[str] = set()
        if sample["scenario_type"] == "intent_override":
            old_value = str(behavior.get("override", {}).get("old_value", ""))
            if old_value:
                excluded_soft.add(old_value)
        requirements = Requirements(
            category=coarse_category(categories.get(target, [])),
            hard_constraints=tuple(str(value) for value in card.get("hard_constraints", [])),
            soft_preferences=tuple(
                str(value)
                for value in card.get("soft_preferences", [])
                if str(value) not in excluded_soft
            ),
        )
        candidate_set = generator.generate(
            requirements,
            session_id=str(sample["sample_id"]),
            turn=3,
        )
        reranked = reranker.rerank(candidate_set, top_k=10)
        top50 = [candidate.parent_asin for candidate in candidate_set.candidates]
        top10 = [candidate.parent_asin for candidate in reranked.ranked_candidates]
        rows.append(
            {
                "sample_id": sample["sample_id"],
                "target_parent_asin": target,
                "top50_rank": top50.index(target) + 1 if target in top50 else None,
                "top10_rank": top10.index(target) + 1 if target in top10 else None,
                "top50": top50,
                "top10": top10,
            }
        )
    summary = {
        "sample_count": len(rows),
        "top50_covered": sum(row["top50_rank"] is not None for row in rows),
        "top10_hit": sum(row["top10_rank"] is not None for row in rows),
        "rows": rows,
        "boundary": "Offline full-card diagnostic; labels are used only in this script.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "rows"}, indent=2))
    if summary["top50_covered"] != args.expect_top50:
        raise SystemExit("Top-50 coverage differs from the locked expectation")
    if summary["top10_hit"] != args.expect_top10:
        raise SystemExit("Top-10 hits differ from the locked expectation")


if __name__ == "__main__":
    main()
