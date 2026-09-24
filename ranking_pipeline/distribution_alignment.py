"""Dataset distribution alignment for the ranking-pipeline training loop."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import statistics
from typing import Any, Mapping, Sequence

from ranking_pipeline.training_data import (
    RerankTrainingExample,
    build_public_training_examples,
    build_synthetic_training_examples,
)


@dataclass(frozen=True)
class DistributionSummary:
    total_examples: int
    positive_examples: int
    negative_examples: int
    public_examples: int
    synthetic_examples: int
    tier_counts: Mapping[str, int]
    weighted_positive_ratio: float


def build_aligned_training_examples(
    public_set_path: str | Path,
    public_top50_path: str | Path | None,
    synthetic_set_path: str | Path,
    catalog_path: str | Path,
    *,
    synthetic_product_csv_path: str | Path | None = None,
    synthetic_tiers_path: str | Path | None = None,
    public_negatives_per_positive: int = 4,
    public_top_k: int = 0,
    public_retrieval_mode: str = "lite",
    synthetic_negatives_per_positive: int = 4,
    synthetic_hard_negatives_per_positive: int = 0,
    synthetic_retrieval_mode: str = "lite",
    public_positive_weight: float = 5.0,
    public_negative_weight: float = 1.0,
    synthetic_tier_filter: Sequence[str] = ("high_confidence", "probable"),
    seed: int = 0,
    limit: int | None = None,
) -> list[RerankTrainingExample]:
    """Combine public gold pairs with synthetic weak-supervision pairs.

    The public set is weighted higher because it is the only organizer-provided
    gold signal. The synthetic set contributes distribution breadth and is
    filtered to the recommended high-confidence/probable tiers by default.
    """

    public_examples = build_public_training_examples(
        public_set_path,
        public_top50_path,
        catalog_path,
        negatives_per_positive=public_negatives_per_positive,
        public_top_k=public_top_k,
        public_retrieval_mode=public_retrieval_mode,
        negative_pool_csv_path=synthetic_product_csv_path,
        seed=seed,
        positive_weight=public_positive_weight,
        negative_weight=public_negative_weight,
    )
    public_target_ids = {
        example.parent_asin for example in public_examples if example.label == 1.0
    }
    synthetic_examples = build_synthetic_training_examples(
        synthetic_set_path,
        catalog_path,
        product_csv_path=synthetic_product_csv_path,
        tiers_path=synthetic_tiers_path,
        negatives_per_positive=synthetic_negatives_per_positive,
        hard_negatives_per_positive=synthetic_hard_negatives_per_positive,
        retrieval_mode=synthetic_retrieval_mode,
        seed=seed,
        tier_filter=synthetic_tier_filter,
        exclude_target_ids=tuple(public_target_ids),
    )
    combined = [*public_examples, *synthetic_examples]
    if limit is not None:
        combined = combined[:limit]
    return combined


def summarize_examples(examples: Sequence[RerankTrainingExample]) -> DistributionSummary:
    """Return lightweight distribution diagnostics for training examples."""

    positives = [example for example in examples if example.label > 0.5]
    public_count = sum(example.source == "public" for example in examples)
    synthetic_count = sum(example.source == "synthetic" for example in examples)
    tier_counts = Counter(example.tier for example in examples)
    total_positive_weight = sum(example.weight for example in positives)
    total_weight = sum(example.weight for example in examples)
    weighted_positive_ratio = (
        total_positive_weight / total_weight if total_weight else 0.0
    )
    return DistributionSummary(
        total_examples=len(examples),
        positive_examples=len(positives),
        negative_examples=len(examples) - len(positives),
        public_examples=public_count,
        synthetic_examples=synthetic_count,
        tier_counts=dict(sorted(tier_counts.items())),
        weighted_positive_ratio=round(weighted_positive_ratio, 6),
    )


def summarize_synthetic_rankings(
    records: Sequence[Mapping[str, Any]],
    tiers: Mapping[str, str],
    *,
    top_k: int = 10,
) -> dict[str, Any]:
    """Summarize synthetic 3,021 diagnostics without treating them as official score.

    Expects each record to contain ``sample_id``, ``ground_truth.parent_asin``,
    ``ranked_ids``, optional parallel ``scores``, and optional ``over_general``.
    The output is intentionally diagnostic: per-tier recall at ``top_k``, score
    distribution, and over-general rate.
    """

    record_items = list(records)
    tier_totals: dict[str, int] = defaultdict(int)
    tier_hits: dict[str, int] = defaultdict(int)
    all_scores: list[float] = []
    over_general_count = 0
    ranked_count = 0
    for record in record_items:
        sample_id = str(record.get("sample_id") or "").strip()
        if not sample_id:
            continue
        ground_truth = record.get("ground_truth") or {}
        target = str(ground_truth.get("parent_asin") or "").strip()
        ranked = [str(value) for value in record.get("ranked_ids") or []]
        tier = tiers.get(sample_id, "unknown")
        tier_totals[tier] += 1
        if ranked:
            ranked_count += 1
        if target and target in ranked[:top_k]:
            tier_hits[tier] += 1
        scores = record.get("scores")
        if isinstance(scores, list):
            for value in scores:
                try:
                    all_scores.append(float(value))
                except (TypeError, ValueError):
                    continue
        over_general = record.get("over_general")
        if over_general is None and isinstance(scores, list) and len(scores) >= 2:
            try:
                margin = (float(scores[0]) - float(scores[1])) / max(abs(float(scores[0])), 1e-9)
                over_general = margin < 0.18
            except (TypeError, ValueError, ZeroDivisionError):
                over_general = False
        if bool(over_general):
            over_general_count += 1
    tier_metrics = {
        tier: {
            "count": tier_totals[tier],
            "hits": tier_hits[tier],
            "recall": round(tier_hits[tier] / tier_totals[tier], 6) if tier_totals[tier] else 0.0,
        }
        for tier in sorted(tier_totals)
    }
    score_distribution = {
        "count": len(all_scores),
        "min": round(min(all_scores), 6) if all_scores else 0.0,
        "max": round(max(all_scores), 6) if all_scores else 0.0,
        "mean": round(statistics.fmean(all_scores), 6) if all_scores else 0.0,
        "median": round(statistics.median(all_scores), 6) if all_scores else 0.0,
    }
    if len(all_scores) >= 2:
        score_distribution["stdev"] = round(statistics.pstdev(all_scores), 6)
    return {
        "top_k": top_k,
        "record_count": len(record_items),
        "ranked_count": ranked_count,
        "over_general_count": over_general_count,
        "over_general_rate": round(over_general_count / len(record_items), 6)
        if record_items
        else 0.0,
        "tier_metrics": tier_metrics,
        "score_distribution": score_distribution,
    }


__all__ = [
    "DistributionSummary",
    "build_aligned_training_examples",
    "summarize_examples",
    "summarize_synthetic_rankings",
]
