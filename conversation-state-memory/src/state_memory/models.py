from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ConstraintType(str, Enum):
    HARD = "hard"
    SOFT = "soft"


class Intent(str, Enum):
    BUYING = "buying"
    BROWSING = "browsing"
    COMPARE = "compare"
    UNKNOWN = "unknown"


class Route(str, Enum):
    BUYING_FILTER = "buying_filter"
    BROWSING_DENSE = "browsing_dense"


class NextAction(str, Enum):
    RETRIEVE_BUYING = "retrieve_buying"
    RETRIEVE_BROWSING = "retrieve_browsing"
    ASK_CLARIFICATION = "ask_clarification"
    REROUTE = "reroute"
    COMPARE = "compare"
    CONVERT = "convert"


@dataclass
class Slot:
    value: Any
    source_turn: int
    confidence: float = 1.0
    priority: float = 1.0
    constraint_type: ConstraintType = ConstraintType.HARD
    evidence: str = ""


@dataclass
class SessionState:
    session_id: str
    turn_id: int = 0
    intent: Intent = Intent.UNKNOWN
    intent_confidence: float = 0.0
    hard_slots: dict[str, Slot] = field(default_factory=dict)
    soft_slots: dict[str, Slot] = field(default_factory=dict)
    rejected_values: dict[str, list[Any]] = field(default_factory=dict)
    rejected_asins: list[str] = field(default_factory=list)
    shown_asins: list[str] = field(default_factory=list)
    candidate_count: int | None = None
    clarification_count: int = 0
    last_clarification_turn: int | None = None
    last_clarification_slot: str | None = None
    summary: str = "No shopping requirement captured yet."
    decision_stage: str = "discovery"


@dataclass
class UserProfile:
    user_id: str
    stable_preferences: dict[str, list[Any]] = field(default_factory=dict)
    preference_confidence: dict[str, float] = field(default_factory=dict)
    observations: dict[str, dict[str, int]] = field(default_factory=dict)
    profile_summary: str = "No stable preferences established."


@dataclass
class StateDelta:
    intent_changed: bool = False
    category_overridden: bool = False
    added_slots: list[str] = field(default_factory=list)
    updated_slots: list[str] = field(default_factory=list)
    erased_slots: list[str] = field(default_factory=list)
    rejected_values: list[str] = field(default_factory=list)


@dataclass
class ContextSnapshot:
    query: str
    intent: Intent
    route: Route
    action: NextAction
    must_match: dict[str, Any]
    should_match: dict[str, dict[Any, float]]
    must_not_match: dict[str, list[Any]]
    profile_hints: dict[str, list[Any]]
    clarification_question: str | None
    retrieval_budget: int
    session_summary: str
    debug: dict[str, Any] = field(default_factory=dict)
