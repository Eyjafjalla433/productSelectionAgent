"""Diagnose synthetic 3,021 rankings without promoting them to official score.

Official metrics are reported only on the public 200 set. This CLI consumes a
ranked-prediction JSONL for the synthetic set and emits per-tier recall, score
distribution, and over-general rate diagnostics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from techjam_agent.contracts import Candidate, CandidateSet, Requirements

from ranking_pipeline.contextual_ranking import HybridContextualReranker
from ranking_pipeline.distribution_alignment import summarize_synthetic_rankings
from ranking_pipeline.training_data import load_catalog, requirements_from_product


ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC_ROOT = ROOT / "synthetic-data-3021" / "data"
DEFAULT_SYNTHETIC_SET = SYNTHETIC_ROOT / "synthetic_contract_matched_all_3021.jsonl"
DEFAULT_TIERS = SYNTHETIC_ROOT / "synthetic_contract_matched_all_3021_tiers.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_tiers(path: Path) -> dict[str, str]:
    tiers: dict[str, str] = {}
    for row in load_jsonl(path):
        sample_id = str(row.get("sample_id") or "").strip()
        tier = str(row.get("quality_tier") or "").strip()
        if sample_id and tier:
            tiers[sample_id] = tier
    return tiers


def load_ground_truth(path: Path) -> dict[str, dict]:
    labels: dict[str, dict] = {}
    for row in load_jsonl(path):
        sample_id = str(row.get("sample_id") or "").strip()
        ground_truth = row.get("ground_truth")
        if sample_id and isinstance(ground_truth, dict):
            labels[sample_id] = ground_truth
    return labels


def load_top_ranking(path: Path) -> dict[str, list[str]]:
    ranking: dict[str, list[str]] = {}
    for row in load_jsonl(path):
        sample_id = str(row.get("sample_id") or "").strip()
        parent_asins = row.get("parent_asins") or []
        if sample_id:
            ranking[sample_id] = [str(value) for value in parent_asins]
    return ranking


def build_records_from_precomputed(
    top50_path: Path,
    catalog_path: Path,
    synthetic_set_path: Path,
    *,
    top_k: int,
) -> list[dict]:
    """Turn precomputed Top50 candidate pools into reranker diagnostic records."""

    catalog = load_catalog(catalog_path)
    top50 = load_top_ranking(top50_path)
    labels = load_ground_truth(synthetic_set_path)
    reranker = HybridContextualReranker(llm_ranker=None, top_n=20, min_keep=15)
    records: list[dict] = []

    def public_product(product: dict) -> dict:
        return {
            field: product[field]
            for field in ("title", "categories", "features", "details", "description", "store")
            if field in product
        }

    for sample_id, ground_truth in labels.items():
        target = str(ground_truth.get("parent_asin") or "")
        candidate_ids = top50.get(sample_id, [])[:50]
        if len(candidate_ids) != 50:
            continue
        candidates: list[Candidate] = []
        for rank, parent_asin in enumerate(candidate_ids, start=1):
            product = catalog.get(parent_asin)
            if product is None:
                continue
            candidates.append(
                Candidate(
                    parent_asin=parent_asin,
                    candidate_rank=rank,
                    source_ranks={"precomputed": rank},
                    product=public_product(product),
                )
            )
        if len(candidates) != 50:
            continue
        target_product = catalog.get(target)
        requirements = (
            requirements_from_product(target_product)
            if target_product is not None
            else Requirements("clothing item", (), ())
        )
        candidate_set = CandidateSet(
            candidate_set_id=sample_id,
            session_id=sample_id,
            turn=3,
            requirements=requirements,
            candidates=tuple(candidates),
        )
        result = reranker.rerank(candidate_set, top_k=top_k)
        pool_metrics = reranker.last_pool_metrics
        records.append(
            {
                "sample_id": sample_id,
                "ground_truth": ground_truth,
                "ranked_ids": [
                    candidate.parent_asin for candidate in result.ranked_candidates
                ],
                "scores": [
                    candidate.score for candidate in result.ranked_candidates
                ],
                "over_general": bool(pool_metrics.is_over_general)
                if pool_metrics is not None
                else False,
            }
        )
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rankings", type=Path, default=None)
    parser.add_argument(
        "--from-precomputed-top50",
        type=Path,
        default=SYNTHETIC_ROOT.parent.parent / "retrieval-and-reranking" / "data"
        / "techjam-precomputed-rankings-200-and-3021" / "synthetic3021_top50.jsonl",
    )
    parser.add_argument("--catalog", type=Path, default=None)
    parser.add_argument("--synthetic-set", type=Path, default=DEFAULT_SYNTHETIC_SET)
    parser.add_argument("--tiers", type=Path, default=DEFAULT_TIERS)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.rankings is not None:
        records = load_jsonl(args.rankings)
        ground_truth_by_id = load_ground_truth(args.synthetic_set)
        for record in records:
            sample_id = str(record.get("sample_id") or "").strip()
            if sample_id and not record.get("ground_truth"):
                record["ground_truth"] = ground_truth_by_id.get(sample_id, {})
    else:
        catalog_path = args.catalog or (
            ROOT / "retrieval-and-reranking" / "data" / "catalog.jsonl"
        )
        records = build_records_from_precomputed(
            args.from_precomputed_top50,
            catalog_path,
            args.synthetic_set,
            top_k=args.top_k,
        )
    tiers = load_tiers(args.tiers)
    result = summarize_synthetic_rankings(records, tiers, top_k=args.top_k)
    text = json.dumps(result, indent=2) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
