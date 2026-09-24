"""Stable boundary between Top-50 generation and Top-10 reranking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


CANDIDATE_CAP = 50
PRODUCT_FIELDS = frozenset(
    {"title", "categories", "features", "details", "description", "store"}
)


@dataclass(frozen=True)
class Requirements:
    """Requirements disclosed through user messages received so far."""

    category: str
    hard_constraints: tuple[str, ...]
    soft_preferences: tuple[str, ...]


@dataclass(frozen=True)
class Candidate:
    """One catalog product and its target-free retrieval provenance."""

    parent_asin: str
    candidate_rank: int
    source_ranks: Mapping[str, int]
    product: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.parent_asin:
            raise ValueError("parent_asin must not be empty")
        if self.candidate_rank < 1:
            raise ValueError("candidate_rank must be positive")
        if any(rank < 1 for rank in self.source_ranks.values()):
            raise ValueError("source ranks must be positive")
        if set(self.product) - PRODUCT_FIELDS:
            raise ValueError("candidate product contains fields outside the public snapshot")


@dataclass(frozen=True)
class CandidateSet:
    """The only value that may cross from Top-50 generation to reranking."""

    candidate_set_id: str
    session_id: str
    turn: int
    requirements: Requirements
    candidates: tuple[Candidate, ...]

    def __post_init__(self) -> None:
        if not self.candidate_set_id or not self.session_id or self.turn < 1:
            raise ValueError("CandidateSet identity is invalid")
        if len(self.candidates) != CANDIDATE_CAP:
            raise ValueError(f"CandidateSet must contain exactly {CANDIDATE_CAP} candidates")
        identifiers = [candidate.parent_asin for candidate in self.candidates]
        if len(set(identifiers)) != CANDIDATE_CAP:
            raise ValueError("CandidateSet parent_asin values must be unique")
        ranks = [candidate.candidate_rank for candidate in self.candidates]
        if ranks != list(range(1, CANDIDATE_CAP + 1)):
            raise ValueError("CandidateSet ranks must be continuous from 1 through 50")


@dataclass(frozen=True)
class RankedCandidate:
    parent_asin: str
    rank: int
    score: float
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class RerankResult:
    candidate_set_id: str
    ranked_candidates: tuple[RankedCandidate, ...]

    def validate_against(self, candidate_set: CandidateSet, *, top_k: int) -> None:
        if self.candidate_set_id != candidate_set.candidate_set_id:
            raise ValueError("RerankResult belongs to another CandidateSet")
        if len(self.ranked_candidates) != top_k:
            raise ValueError(f"RerankResult must contain exactly {top_k} candidates")
        selected = [candidate.parent_asin for candidate in self.ranked_candidates]
        allowed = {candidate.parent_asin for candidate in candidate_set.candidates}
        if len(set(selected)) != top_k or set(selected) - allowed:
            raise ValueError("RerankResult must be a unique subset of CandidateSet")
        if [candidate.rank for candidate in self.ranked_candidates] != list(
            range(1, top_k + 1)
        ):
            raise ValueError("RerankResult ranks must be continuous")


class Top50CandidateGenerator(Protocol):
    def generate(
        self,
        requirements: Requirements,
        *,
        session_id: str,
        turn: int,
    ) -> CandidateSet:
        """Return exactly 50 candidates from disclosed requirements."""


class Top10Reranker(Protocol):
    def rerank(self, candidate_set: CandidateSet, *, top_k: int) -> RerankResult:
        """Return an ordered subset without retrieving new products."""
