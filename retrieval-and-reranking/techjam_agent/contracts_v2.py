"""Variable-size 2 -> 4 contracts, independent of the legacy Top50 algorithms.

Legacy CandidateSet/RerankResult remain unchanged. Adapters below only wrap
existing output: no padding, filtering, reordering, or inferred statistics.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping, Protocol

from .contracts import Candidate, CandidateSet, RankedCandidate, RerankResult, Requirements


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class RetrievalStats:
    """Unique counts before/after hard filtering, BEFORE Top-N truncation.

    None is unknown. matched_count is the deduplicated union of retrieval
    routes, not the sum of their individual depths or the full catalog size.
    """

    matched_count: int | None = None
    filtered_count: int | None = None

    def __post_init__(self) -> None:
        for count in (self.matched_count, self.filtered_count):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("counts must be nonnegative integers or unknown")
        if self.matched_count is not None and self.filtered_count is not None:
            if self.filtered_count > self.matched_count:
                raise ValueError("filtering cannot increase the unique candidate count")


@dataclass(frozen=True)
class RetrievalResultV2:
    candidate_set_id: str
    session_id: str
    turn: int
    state_version: int
    candidate_limit: int
    candidates: tuple[Candidate, ...]
    stats: RetrievalStats = field(default_factory=RetrievalStats)
    # Structured full-state payload travels intact, separate from legacy text.
    state_snapshot: Mapping[str, Any] | None = None
    legacy_requirements: Requirements | None = None
    warnings: tuple[str, ...] = ()
    schema_version: str = field(default="2.0", init=False)

    def __post_init__(self) -> None:
        if not self.candidate_set_id.strip() or not self.session_id.strip():
            raise ValueError("candidate_set_id and session_id must not be empty")
        for name in ("turn", "state_version", "candidate_limit"):
            _positive_integer(getattr(self, name), name)
        object.__setattr__(self, "candidates", deepcopy(tuple(self.candidates)))
        object.__setattr__(self, "state_snapshot", deepcopy(self.state_snapshot))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        if len(self.candidates) > self.candidate_limit:
            raise ValueError("candidate count exceeds the requested limit")
        identifiers = [item.parent_asin for item in self.candidates]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("candidate identifiers must be unique")
        if [item.candidate_rank for item in self.candidates] != list(range(1, len(self.candidates) + 1)):
            raise ValueError("candidate ranks must be continuous")
        for count in (self.stats.matched_count, self.stats.filtered_count):
            if count is not None and count < len(self.candidates):
                raise ValueError("pre-truncation count cannot be less than returned count")
        if self.state_snapshot is not None:
            if self.state_snapshot.get("schema_version") != "2.0":
                raise ValueError("expected a version 2.0 state snapshot")
            if any(self.state_snapshot.get(name) != getattr(self, name) for name in ("session_id", "turn", "state_version")):
                raise ValueError("state snapshot belongs to another session, turn or version")

    @property
    def returned_count(self) -> int:
        return len(self.candidates)

    def validate_context(self, *, session_id: str, turn: int, state_version: int) -> None:
        if (self.session_id, self.turn, self.state_version) != (session_id, turn, state_version):
            raise ValueError("stale or cross-session retrieval result")

    @classmethod
    def from_legacy(
        cls, result: CandidateSet, *, state_version: int,
        state_snapshot: Mapping[str, Any] | None = None,
    ) -> "RetrievalResultV2":
        return cls(
            candidate_set_id=result.candidate_set_id,
            session_id=result.session_id,
            turn=result.turn,
            state_version=state_version,
            candidate_limit=len(result.candidates),
            candidates=result.candidates,
            state_snapshot=state_snapshot,
            legacy_requirements=result.requirements,
            warnings=("Legacy output: pre-truncation counts and structured constraint enforcement are unverified.",),
        )

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "returned_count": self.returned_count}


@dataclass(frozen=True)
class RankingResultV2:
    candidate_set_id: str
    session_id: str
    turn: int
    state_version: int
    ranked_candidates: tuple[RankedCandidate, ...]
    ranking_method: str
    score_semantics: str = "uncalibrated"
    schema_version: str = field(default="2.0", init=False)

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.candidate_set_id, self.session_id, self.ranking_method, self.score_semantics)):
            raise ValueError("ranking identity, method and score semantics are required")
        _positive_integer(self.turn, "turn")
        _positive_integer(self.state_version, "state_version")
        object.__setattr__(self, "ranked_candidates", deepcopy(tuple(self.ranked_candidates)))
        ids = [item.parent_asin for item in self.ranked_candidates]
        if any(not value.strip() for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("ranked identifiers must be nonempty and unique")
        if [item.rank for item in self.ranked_candidates] != list(range(1, len(ids) + 1)):
            raise ValueError("ranking positions must be continuous")
        if any(not math.isfinite(item.score) for item in self.ranked_candidates):
            raise ValueError("ranking scores must be finite")

    def validate_against(self, result: RetrievalResultV2, *, top_k: int) -> None:
        _positive_integer(top_k, "top_k")
        result.validate_context(session_id=self.session_id, turn=self.turn, state_version=self.state_version)
        if self.candidate_set_id != result.candidate_set_id:
            raise ValueError("ranking belongs to another candidate set")
        if len(self.ranked_candidates) > min(top_k, result.returned_count):
            raise ValueError("ranking exceeds top_k or available candidates")
        allowed = {item.parent_asin for item in result.candidates}
        if any(item.parent_asin not in allowed for item in self.ranked_candidates):
            raise ValueError("ranking must be a subset of retrieved candidates")

    @classmethod
    def from_legacy(
        cls, result: RerankResult, *, retrieval: RetrievalResultV2,
        top_k: int, ranking_method: str,
    ) -> "RankingResultV2":
        converted = cls(
            candidate_set_id=result.candidate_set_id,
            session_id=retrieval.session_id,
            turn=retrieval.turn,
            state_version=retrieval.state_version,
            ranked_candidates=result.ranked_candidates,
            ranking_method=ranking_method,
        )
        converted.validate_against(retrieval, top_k=top_k)
        return converted

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VariableCandidateReranker(Protocol):
    """Future implementation interface; legacy rerankers do not implement it."""

    def rerank(self, result: RetrievalResultV2, *, top_k: int) -> RankingResultV2:
        ...
