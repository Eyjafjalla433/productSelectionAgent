"""Deterministic public and synthetic training data for the Qwen3 reranker.

The evaluator materializes hidden requirements from product metadata. For local
reranker training we reproduce that public logic from the participant kit, then
join the already-materialized Top50 candidate list with the frozen catalog.
The organizer's private 800 sessions are never used and no private label is
reconstructed. The synthetic 3,021 sessions are used only as a public-schema
compatible local proxy for distribution alignment and are never reported as an
official generalization result.
"""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from techjam_agent.contracts import Requirements
from ranking_pipeline.qwen_reranker import format_pair, product_text, query_text


MATERIALS = (
    "cotton",
    "polyester",
    "nylon",
    "leather",
    "wool",
    "spandex",
    "silk",
    "rayon",
    "fabric",
)
COLORS = (
    "black",
    "white",
    "blue",
    "red",
    "pink",
    "green",
    "brown",
    "gray",
    "grey",
    "purple",
    "yellow",
    "orange",
)
SEARCH_FIELDS = ("title", "features", "details", "description", "categories", "store")
MATERIAL_RE = re.compile(rf"\b({'|'.join(MATERIALS)})\b", re.I)
COLOR_RE = re.compile(rf"\b({'|'.join(COLORS)})\b", re.I)


def _flatten_values(value: object) -> list[str]:
    if isinstance(value, dict):
        return [f"{key}: {item}" for key, item in value.items() if item not in (None, "", [])]
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)] if value not in (None, "") else []


def searchable_text(product: dict) -> str:
    parts: list[str] = []
    for field in SEARCH_FIELDS:
        value = product.get(field)
        if isinstance(value, dict):
            parts.extend(f"{key} {item}" for key, item in value.items())
        elif isinstance(value, list):
            parts.extend(str(item) for item in value)
        elif value is not None:
            parts.append(str(value))
    return " ".join(parts).strip()


def clean_constraint(value: str, limit: int = 180) -> str:
    return re.sub(r"\s+", " ", value).strip(" -;,.\t\n")[:limit].rstrip()


def intent_card(product: dict, limit: int = 180) -> dict:
    title = clean_constraint(str(product.get("title") or "product"), limit)
    candidates = [
        *_flatten_values(product.get("features")),
        *_flatten_values(product.get("details")),
    ]
    corpus = searchable_text(product)
    material = MATERIAL_RE.search(corpus)
    color = COLOR_RE.search(corpus)
    if material:
        candidates.insert(0, material.group(1).lower())
    if color:
        candidates.insert(1, f"color: {color.group(1).lower()}")
    if product.get("price") not in (None, ""):
        candidates.append(f"budget around ${product['price']}")
    cleaned = list(
        dict.fromkeys(clean_constraint(item, limit) for item in candidates if clean_constraint(item, limit))
    )
    if not cleaned:
        cleaned = [title]
    return {
        "target_category": title,
        "hard_constraints": cleaned[:2],
        "soft_preferences": cleaned[2:4] or cleaned[:1],
    }


def requirements_from_product(product: dict) -> Requirements:
    card = intent_card(product)
    return Requirements(
        category=card["target_category"],
        hard_constraints=tuple(card["hard_constraints"]),
        soft_preferences=tuple(card["soft_preferences"]),
    )


@dataclass(frozen=True)
class RerankTrainingExample:
    query: str
    document: str
    label: float
    parent_asin: str
    group_id: str = ""
    weight: float = 1.0
    source: str = "public"
    tier: str = "public"


def load_jsonl(path: str | Path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def load_catalog(path: str | Path) -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            product = json.loads(line)
            catalog[str(product["parent_asin"])] = product
    return catalog


def load_top50(path: str | Path) -> dict[str, list[str]]:
    top50: dict[str, list[str]] = {}
    for row in load_jsonl(path):
        top50[str(row["sample_id"])] = [str(value) for value in row["parent_asins"]]
    return top50


def _sample_negative_candidates(
    target_asin: str,
    target_product: dict,
    catalog: Mapping[str, dict],
    product_pool: Mapping[str, Mapping[str, str]] | None,
    rng: random.Random,
    count: int,
) -> list[str]:
    """Return deterministic negatives from the pool when no Top50 file exists."""

    if product_pool:
        target_categories = {
            str(value).strip().lower()
            for value in (target_product.get("categories") or [])
            if str(value).strip()
        }
        candidates = [
            parent_asin
            for parent_asin, metadata in product_pool.items()
            if parent_asin != target_asin
            and any(
                category in target_categories
                for category in (
                    metadata.get("leaf_category", "").lower(),
                    metadata.get("family", "").lower(),
                )
            )
        ]
        if not candidates:
            candidates = [
                parent_asin for parent_asin in product_pool if parent_asin != target_asin
            ]
    else:
        candidates = [parent_asin for parent_asin in catalog if parent_asin != target_asin]
    rng.shuffle(candidates)
    return candidates[:count]


def _candidate_product(parent_asin: str, product: dict):
    """Wrap a catalog row into the minimal Candidate-like shape expected by helpers."""

    from techjam_agent.contracts import Candidate

    public_product = {
        field: product.get(field)
        for field in ("title", "categories", "features", "details", "description", "store")
    }
    return Candidate(
        parent_asin=parent_asin,
        candidate_rank=1,
        source_ranks={"train": 1},
        product=public_product,
    )


def examples_to_pairs(examples: Iterable[RerankTrainingExample]) -> list[tuple[str, str]]:
    return [(example.query, example.document) for example in examples]


def load_product_weak_supervision(path: str | Path) -> dict[str, dict[str, str]]:
    """Load product-level weak pool supervision from a CSV file.

    Expected columns include ``parent_asin``, ``family``, ``leaf_category``,
    ``quality_tier``, and ``selection_frequency``. The returned dictionary is
    keyed by parent_asin and keeps only string fields, so callers can use it as
    a deterministic negative-sampling pool without depending on the old
    precomputed Top50 directories.
    """

    records: dict[str, dict[str, str]] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            parent_asin = str(row.get("parent_asin") or "").strip()
            if not parent_asin:
                continue
            records[parent_asin] = {
                "family": str(row.get("family") or "").strip(),
                "leaf_category": str(row.get("leaf_category") or "").strip(),
                "quality_tier": str(row.get("quality_tier") or "").strip(),
                "selection_frequency": str(row.get("selection_frequency") or "").strip(),
            }
    return records


def load_tier_weights(path: str | Path) -> dict[str, str]:
    """Return ``sample_id -> quality_tier`` from the synthetic tiers JSONL."""

    tiers: dict[str, str] = {}
    for row in load_jsonl(path):
        sample_id = str(row.get("sample_id") or "").strip()
        tier = str(row.get("quality_tier") or "").strip()
        if sample_id and tier:
            tiers[sample_id] = tier
    return tiers


def default_tier_weight(tier: str) -> float:
    """Conservative weights for positive-unlabeled synthetic tiers.

    ``high_confidence`` and ``probable`` are the recommended training pool.
    Lower tiers are useful for calibration and hard negatives but are not
    official private-800 labels.
    """

    return {
        "high_confidence": 1.0,
        "probable": 0.6,
        "uncertain": 0.25,
        "low_likelihood": 0.1,
    }.get(tier, 0.25)


def _product_negative_weight(metadata: Mapping[str, str]) -> float:
    """Weight a synthetic negative by product tier and selection frequency.

    Selection frequency approximates how often a product appears in retrieval
    candidate pools. Combining it with quality tier keeps the sampled negatives
    distributionally close to the real retrieval distribution while still
    suppressing noisy/low-confidence catalog rows.
    """

    tier_weight = default_tier_weight(metadata.get("quality_tier") or "unknown")
    try:
        frequency = float(metadata.get("selection_frequency") or 1.0)
    except ValueError:
        frequency = 1.0
    return max(0.01, tier_weight * max(0.0, frequency))


def _weighted_sample_ids(
    candidate_ids: Sequence[str],
    weights: Sequence[float],
    count: int,
    rng: random.Random,
) -> list[str]:
    """Sample unique IDs without replacement using deterministic weights."""

    if not candidate_ids or count <= 0:
        return []
    remaining = list(candidate_ids)
    remaining_weights = list(weights)
    selected: list[str] = []
    while remaining and len(selected) < count:
        if any(weight > 0 for weight in remaining_weights):
            choice = rng.choices(range(len(remaining)), weights=remaining_weights, k=1)[0]
        else:
            choice = rng.randrange(len(remaining))
        selected.append(remaining.pop(choice))
        remaining_weights.pop(choice)
    return selected


def build_public_training_examples(
    public_set_path: str | Path,
    public_top50_path: str | Path | None,
    catalog_path: str | Path,
    *,
    negatives_per_positive: int = 4,
    public_top_k: int = 0,
    public_retrieval_mode: str = "lite",
    negative_pool_csv_path: str | Path | None = None,
    seed: int = 0,
    limit: int | None = None,
    positive_weight: float = 1.0,
    negative_weight: float = 1.0,
) -> list[RerankTrainingExample]:
    """Build public-200 examples, optionally using a frozen Top50 candidate file.

    When ``public_top50_path`` is missing, negatives are sampled from the weak
    supervision CSV or the catalog itself so the training loop no longer depends
    on the old precomputed Top50 directory.
    """

    samples = load_jsonl(public_set_path)
    top50 = (
        load_top50(public_top50_path)
        if public_top50_path is not None and Path(public_top50_path).is_file()
        else {}
    )
    catalog = load_catalog(catalog_path)
    product_pool = (
        load_product_weak_supervision(negative_pool_csv_path)
        if negative_pool_csv_path is not None
        else None
    )
    candidate_generator = None
    if public_top_k > 0:
        if public_retrieval_mode == "exact":
            from techjam_agent.retrieval import ExactDenseTop50CandidateGenerator

            candidate_generator = ExactDenseTop50CandidateGenerator(catalog_path)
        else:
            from techjam_agent.retrieval import LiteTop50CandidateGenerator

            candidate_generator = LiteTop50CandidateGenerator(catalog_path)
    rng = random.Random(seed)
    examples: list[RerankTrainingExample] = []
    for sample in samples:
        sample_id = str(sample["sample_id"])
        target = str(sample["ground_truth"]["parent_asin"])
        target_product = catalog.get(target)
        if target_product is None:
            continue
        requirements = requirements_from_product(target_product)
        if candidate_generator is not None:
            candidate_set = candidate_generator.generate(
                requirements,
                session_id=sample_id,
                turn=3,
            )
            candidates = [
                candidate.parent_asin for candidate in candidate_set.candidates
            ]
        else:
            candidates = top50.get(sample_id, [])
        if candidates:
            negatives = [item for item in candidates if item != target]
            if candidate_generator is not None:
                negatives = negatives[:public_top_k]
        else:
            negatives = _sample_negative_candidates(
                target,
                target_product,
                catalog,
                product_pool,
                rng,
                negatives_per_positive,
            )
        query = query_text(requirements)
        examples.append(
            RerankTrainingExample(
                query=query,
                document=product_text(_candidate_product(target, target_product)),
                label=1.0,
                parent_asin=target,
                group_id=sample_id,
                weight=positive_weight,
                source="public",
                tier="public",
            )
        )
        rng.shuffle(negatives)
        max_negatives = max(negatives_per_positive, public_top_k) if public_top_k else negatives_per_positive
        for negative_id in negatives[:max_negatives]:
            negative_product = catalog.get(negative_id)
            if negative_product is None:
                continue
            examples.append(
                RerankTrainingExample(
                    query=query,
                    document=product_text(_candidate_product(negative_id, negative_product)),
                    label=0.0,
                    parent_asin=negative_id,
                    group_id=sample_id,
                    weight=negative_weight,
                    source="public",
                    tier="public",
                )
            )
        if limit is not None and len(examples) >= limit:
            break
    return examples


def build_synthetic_training_examples(
    synthetic_set_path: str | Path,
    catalog_path: str | Path,
    *,
    product_csv_path: str | Path | None = None,
    tiers_path: str | Path | None = None,
    negatives_per_positive: int = 4,
    hard_negatives_per_positive: int = 0,
    retrieval_mode: str = "lite",
    seed: int = 0,
    limit: int | None = None,
    tier_filter: Sequence[str] = ("high_confidence", "probable"),
    positive_weight: float | None = None,
    negative_weight: float = 0.5,
    exclude_target_ids: Sequence[str] = (),
) -> list[RerankTrainingExample]:
    """Build synthetic proxy examples without depending on old Top50 files.

    The target product is positive. When ``hard_negatives_per_positive`` is
    greater than zero, the first negatives are taken from the retrieval stage's
    Top-K output, which provides harder, retrieval-confusable examples. The
    remaining negatives are filled from the weak-supervision CSV's same
    leaf-category pool.
    """

    samples = load_jsonl(synthetic_set_path)
    catalog = load_catalog(catalog_path)
    tiers = load_tier_weights(tiers_path) if tiers_path is not None else {}
    product_pool = (
        load_product_weak_supervision(product_csv_path)
        if product_csv_path is not None
        else {}
    )
    allowed_tiers = set(tier_filter)
    excluded_targets = set(exclude_target_ids)
    rng = random.Random(seed)
    examples: list[RerankTrainingExample] = []
    candidate_generator = None
    if hard_negatives_per_positive > 0:
        if retrieval_mode == "exact":
            from techjam_agent.retrieval import ExactDenseTop50CandidateGenerator

            candidate_generator = ExactDenseTop50CandidateGenerator(catalog_path)
        else:
            from techjam_agent.retrieval import LiteTop50CandidateGenerator

            candidate_generator = LiteTop50CandidateGenerator(catalog_path)
    total_negatives = max(negatives_per_positive, hard_negatives_per_positive)

    by_category: dict[str, list[str]] = {}
    for parent_asin, metadata in product_pool.items():
        category = metadata.get("leaf_category") or "unknown"
        by_category.setdefault(category, []).append(parent_asin)

    for sample in samples:
        sample_id = str(sample["sample_id"])
        tier = tiers.get(sample_id)
        if allowed_tiers and (tier is None or tier not in allowed_tiers):
            continue
        target = str(sample["ground_truth"]["parent_asin"])
        if target in excluded_targets:
            continue
        target_product = catalog.get(target)
        if target_product is None:
            continue
        target_weight = (
            default_tier_weight(tier)
            if positive_weight is None and tier is not None
            else (positive_weight if positive_weight is not None else 1.0)
        )
        requirements = requirements_from_product(target_product)
        query = query_text(requirements)
        examples.append(
            RerankTrainingExample(
                query=query,
                document=product_text(_candidate_product(target, target_product)),
                label=1.0,
                parent_asin=target,
                group_id=sample_id,
                weight=target_weight,
                source="synthetic",
                tier=tier or "unknown",
            )
        )

        hard_negatives: list[str] = []
        if candidate_generator is not None:
            try:
                candidate_set = candidate_generator.generate(
                    requirements,
                    session_id=sample_id,
                    turn=3,
                )
                hard_negatives = [
                    candidate.parent_asin
                    for candidate in candidate_set.candidates
                    if candidate.parent_asin != target
                ]
            except Exception:
                hard_negatives = []

        negatives = [
            parent_asin
            for parent_asin in hard_negatives
            if parent_asin != target
            and parent_asin not in excluded_targets
            and parent_asin in catalog
        ][:hard_negatives_per_positive]

        target_metadata = product_pool.get(target, {})
        category = target_metadata.get("leaf_category") or "unknown"
        same_category = [item for item in by_category.get(category, []) if item != target]
        same_category = [item for item in same_category if item not in set(negatives)]
        same_category_weights = [
            _product_negative_weight(product_pool.get(item, {}))
            for item in same_category
        ]
        negatives.extend(
            _weighted_sample_ids(
                same_category,
                same_category_weights,
                total_negatives - len(negatives),
                rng,
            )
        )
        if len(negatives) < total_negatives and product_pool:
            other = [item for item in product_pool if item != target]
            other = [item for item in other if item not in set(negatives)]
            other_weights = [
                _product_negative_weight(product_pool.get(item, {}))
                for item in other
            ]
            negatives.extend(
                _weighted_sample_ids(
                    other,
                    other_weights,
                    total_negatives - len(negatives),
                    rng,
                )
            )
        for negative_id in negatives[:total_negatives]:
            negative_product = catalog.get(negative_id)
            if negative_product is None:
                continue
            examples.append(
                RerankTrainingExample(
                    query=query,
                    document=product_text(_candidate_product(negative_id, negative_product)),
                    label=0.0,
                    parent_asin=negative_id,
                    group_id=sample_id,
                    weight=negative_weight,
                    source="synthetic",
                    tier=tier or "unknown",
                )
            )
        if limit is not None and len(examples) >= limit:
            break
    return examples
