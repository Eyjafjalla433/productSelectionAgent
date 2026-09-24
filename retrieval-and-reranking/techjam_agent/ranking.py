"""Locked Top-10 weighted reciprocal-rank reranker."""

from __future__ import annotations

from collections.abc import Mapping

from techjam_agent.contracts import Candidate, CandidateSet, RankedCandidate, RerankResult
from techjam_agent.query import parse_intent, tokenize
from techjam_agent.retrieval import (
    CATEGORY_ROOT_TERMS,
    VISIBLE_FIELDS,
    _text,
    evidence_category_terms,
)


ROUTE_NAMES = (
    "color_normalized_full_and",
    "category_gate",
    "category_or",
)
RRF_CONSTANT = 60
LOCKED_WEIGHTS = {
    "structured": 1.0,
    "evidence": 2.0,
    "agreement": 0.125,
}


def _phrases(values: tuple[str, ...]) -> tuple[str, ...]:
    phrases: list[str] = []
    for value in values:
        phrase = " ".join(tokenize(value))
        if len(phrase.split()) >= 3 and phrase not in phrases:
            phrases.append(phrase)
    return tuple(phrases)


def _structured_score(candidate_set: CandidateSet, candidate: Candidate) -> float:
    requirements = candidate_set.requirements
    plan = parse_intent(
        requirements.category,
        requirements.hard_constraints,
        requirements.soft_preferences,
    )
    category_terms = set(evidence_category_terms(requirements.category)) - CATEGORY_ROOT_TERMS
    hard_terms = set(plan.hard_terms)
    soft_terms = set(plan.soft_terms)
    long_phrases = _phrases(requirements.soft_preferences)
    text = " ".join(
        tokenize(" ".join(_text(candidate.product.get(field)) for field in VISIBLE_FIELDS))
    )
    terms = set(tokenize(text))
    category_tokens = set(tokenize(_text(candidate.product.get("categories"))))
    hard_hits = len(hard_terms & terms)
    category_hits = len(category_terms & category_tokens)
    soft_hits = len(soft_terms & terms)
    phrase_hits = sum(phrase in text for phrase in long_phrases)
    return (
        hard_hits / max(1, len(hard_terms)) * 1_000_000
        + category_hits / max(1, len(category_terms)) * 10_000
        + phrase_hits * 100
        + soft_hits / max(1, len(soft_terms)) * 10
        - candidate.candidate_rank * 0.0001
    )


def _rank_map(values: list[str]) -> dict[str, int]:
    return {parent_asin: rank for rank, parent_asin in enumerate(values, start=1)}


class LockedWeightedRrfTop10Reranker:
    """Rerank only CandidateSet; it has no catalog or retrieval dependency."""

    def rerank(self, candidate_set: CandidateSet, *, top_k: int) -> RerankResult:
        if not 1 <= top_k <= len(candidate_set.candidates):
            raise ValueError("top_k must be between 1 and CandidateSet size")
        candidates = list(candidate_set.candidates)
        incoming = {candidate.parent_asin: candidate.candidate_rank for candidate in candidates}
        structured = sorted(
            candidates,
            key=lambda candidate: (
                -_structured_score(candidate_set, candidate),
                candidate.candidate_rank,
            ),
        )
        structured_ranks = _rank_map([candidate.parent_asin for candidate in structured])
        scores: dict[str, float] = {}
        evidence_rows: dict[str, tuple[str, ...]] = {}
        for candidate in candidates:
            source_ranks: Mapping[str, int] = candidate.source_ranks
            route_hits = sum(name in source_ranks for name in ROUTE_NAMES)
            evidence_rank = source_ranks.get("evidence")
            features = {
                "structured": 1.0
                / (RRF_CONSTANT + structured_ranks[candidate.parent_asin]),
                "evidence": (
                    1.0 / (RRF_CONSTANT + evidence_rank)
                    if evidence_rank is not None
                    else 0.0
                ),
                "agreement": (route_hits + int(evidence_rank is not None))
                / (4.0 * (RRF_CONSTANT + 1)),
            }
            scores[candidate.parent_asin] = sum(
                LOCKED_WEIGHTS[name] * value for name, value in features.items()
            )
            evidence_rows[candidate.parent_asin] = (
                f"structured_rank:{structured_ranks[candidate.parent_asin]}",
                f"evidence_rank:{evidence_rank if evidence_rank is not None else 'none'}",
                f"route_hits:{route_hits}",
            )
        ordered = sorted(
            candidates,
            key=lambda candidate: (-scores[candidate.parent_asin], incoming[candidate.parent_asin]),
        )
        ranked_candidates = tuple(
            RankedCandidate(
                parent_asin=candidate.parent_asin,
                rank=rank,
                score=scores[candidate.parent_asin],
                evidence=evidence_rows[candidate.parent_asin],
            )
            for rank, candidate in enumerate(ordered[:top_k], start=1)
        )
        return RerankResult(
            candidate_set_id=candidate_set.candidate_set_id,
            ranked_candidates=ranked_candidates,
        )
