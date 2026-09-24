"""Versioned state export. No parsing, merging, decay or policy execution here."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import math
from typing import Any, Mapping, Protocol

from .models import ContextSnapshot, SessionState


@dataclass(frozen=True)
class WeightedPreference:
    """Keep values as JSON values, not object keys that coerce numbers to text."""

    value: Any
    weight: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.weight):
            raise ValueError("preference weight must be finite")
        object.__setattr__(self, "value", deepcopy(self.value))


@dataclass(frozen=True)
class StateSnapshotV2:
    """Detached full state for downstream consumers, not a per-turn delta.

    state_version is supplied by the orchestrator, never inferred from turn:
    feedback can revise a snapshot without receiving another user message.
    None question history means unavailable, not an empty verified history.
    """

    session_id: str
    turn: int
    state_version: int
    intent: str
    intent_confidence: float
    query: str
    hard_constraints: dict[str, Any]
    soft_preferences: dict[str, tuple[WeightedPreference, ...]]
    exclusions: dict[str, list[Any]]
    slot_metadata: dict[str, dict[str, Any]]
    profile_hints: dict[str, list[Any]]
    session_summary: str
    shown_asins: tuple[str, ...]
    suggestions: dict[str, Any]
    asked_questions: tuple[dict[str, Any], ...] | None = None
    pending_question: dict[str, Any] | None = None
    rejected_asins: tuple[str, ...] = ()
    schema_version: str = field(default="2.0", init=False)

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        if any(type(value) is not int or value < 1 for value in (self.turn, self.state_version)):
            raise ValueError("turn and state_version must be positive integers")
        if self.intent not in {"buying", "browsing", "unknown", "compare"}:
            raise ValueError("unsupported state intent")
        if not 0 <= self.intent_confidence <= 1:
            raise ValueError("invalid intent confidence")
        # Frozen prevents field reassignment; copies also prevent consumers
        # from mutating live manager state through nested containers.
        for name in ("hard_constraints", "soft_preferences", "exclusions", "slot_metadata", "profile_hints", "suggestions", "asked_questions", "pending_question"):
            object.__setattr__(self, name, deepcopy(getattr(self, name)))
        object.__setattr__(self, "shown_asins", tuple(self.shown_asins))
        object.__setattr__(self, "rejected_asins", tuple(self.rejected_asins))

    @classmethod
    def from_legacy(
        cls,
        snapshot: ContextSnapshot,
        *,
        session: SessionState,
        state_version: int,
        asked_questions: tuple[dict[str, Any], ...] | None = None,
        pending_question: dict[str, Any] | None = None,
    ) -> "StateSnapshotV2":
        """Export a snapshot and its matching session without updating either."""
        hard = {name: slot.value for name, slot in session.hard_slots.items()}
        if hard != snapshot.must_match:
            raise ValueError("snapshot and session hard constraints do not match")
        if snapshot.intent != session.intent or snapshot.must_not_match != session.rejected_values:
            raise ValueError("snapshot and session intent or exclusions do not match")
        soft_values = {name: set(values) for name, values in snapshot.should_match.items()}
        if soft_values != {name: {slot.value} for name, slot in session.soft_slots.items()}:
            raise ValueError("snapshot and session soft preferences do not match")
        metadata = {
            tier: {name: {**asdict(slot), "constraint_type": slot.constraint_type.value} for name, slot in slots.items()}
            for tier, slots in (("hard", session.hard_slots), ("soft", session.soft_slots))
        }
        return cls(
            session_id=session.session_id,
            turn=session.turn_id,
            state_version=state_version,
            intent=session.intent.value,
            intent_confidence=session.intent_confidence,
            query=snapshot.query,
            hard_constraints=snapshot.must_match,
            soft_preferences={
                name: tuple(WeightedPreference(value, weight) for value, weight in values.items())
                for name, values in snapshot.should_match.items()
            },
            exclusions=snapshot.must_not_match,
            slot_metadata=metadata,
            profile_hints=snapshot.profile_hints,
            session_summary=snapshot.session_summary,
            shown_asins=tuple(session.shown_asins),
            suggestions={
                "route": snapshot.route.value,
                "action": snapshot.action.value,
                "clarification_question": snapshot.clarification_question,
                "retrieval_budget": snapshot.retrieval_budget,
            },
            asked_questions=asked_questions,
            pending_question=pending_question,
            rejected_asins=tuple(session.rejected_asins),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return an independent payload; price and exclusions stay structured."""
        return asdict(self)


class IntentStateUpdater(Protocol):
    """Receiving interface for IntentResult.to_state_handoff() payloads.

    The existing StateMemoryManager does not implement this yet. A future
    implementation must explicitly handle slot_updates=None (unsupported),
    rather than pretending the user made no changes. No raw-text fallback or
    slot-update algorithm is introduced by this protocol declaration.
    """

    def update_from_intent(self, handoff: Mapping[str, Any]) -> StateSnapshotV2:
        ...
