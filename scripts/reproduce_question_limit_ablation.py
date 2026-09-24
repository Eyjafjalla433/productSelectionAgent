"""Run component 4's one-vs-two fixed-question ablation on public-200."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "retrieval-and-reranking", ROOT / "competition_kit"):
    sys.path.insert(0, str(folder))

from evaluator import local_evaluator
from ranking_pipeline.agent import RankingAgent


EXPECTED = {
    2: (0.985, 0.884625, 3.205, 0.7795, 0.913788),
    1: (0.965, 0.769306, 2.545, 0.8455, 0.882392),
}
METRICS = ("hit_rate_at_10", "mrr", "mttc", "efficiency", "recommended_technical_score")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retrieval-mode", choices=("lite", "exact"), default="lite")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "ranking_pipeline/results")
    parser.add_argument("--no-reference-check", action="store_true")
    args = parser.parse_args()
    catalog = ROOT / "competition_kit/data/catalog.jsonl"
    dataset = ROOT / "competition_kit/data/public_set.jsonl"
    samples = local_evaluator.load_jsonl(dataset)
    ids, categories, products = local_evaluator.catalog_index(catalog)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    for limit in (2, 1):
        agent = RankingAgent(
            catalog, retrieval_mode=args.retrieval_mode, reranker_mode="locked",
            clarification_turn_limit=limit,
        )
        result = local_evaluator.evaluate(agent, samples, ids, categories, products)
        path = args.output_dir / f"question-limit-{limit}-{args.retrieval_mode}.json"
        path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        values = tuple(result[name] for name in METRICS)
        if not args.no_reference_check and values != EXPECTED[limit]:
            raise SystemExit(f"Question limit {limit} differs from the recorded component-4 reference: {values}")
        results[str(limit)] = {name: result[name] for name in METRICS} | {"result": str(path.relative_to(ROOT))}
    change = {name: round(results["1"][name] - results["2"][name], 6) for name in METRICS}
    report = {
        "configuration": {
            "agent": "ranking_pipeline.agent.RankingAgent", "retrieval_mode": args.retrieval_mode,
            "reranker_mode": "locked", "sample_count": len(samples), "official_evaluator_unmodified": True,
        },
        "official_artifact_sha256": {"catalog": sha256(catalog), "public_set": sha256(dataset),
            "evaluator": sha256(ROOT / "competition_kit/evaluator/local_evaluator.py")},
        "question_limit_2": results["2"], "question_limit_1": results["1"], "limit_1_minus_limit_2": change,
        "interpretation": "The Lite run exactly matches the recorded Exact metrics on public-200; this does not prove identical behavior on private sessions.",
    }
    report_path = args.output_dir / f"question-limit-ablation-{args.retrieval_mode}.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
