from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Literal


IntentType = Literal["buying", "browsing"] | None


@dataclass(frozen=True)
class SlotUpdate:
    """Explicit per-turn change for state memory; this class does not parse text.

    ``set`` replaces the current value in the specified hard/soft tier;
    ``clear`` removes both tiers and exclusions for this slot;
    ``exclude`` adds rejected values; ``remove_exclusion`` only allows them again.
    A set does not implicitly remove exclusions: the producer must say so.
    """

    slot: str
    operation: Literal["set", "clear", "exclude", "remove_exclusion"]
    values: tuple[str | float | bool, ...] = ()
    constraint_type: Literal["hard", "soft"] | None = None
    confidence: float | None = None
    evidence: str = ""

    def __post_init__(self) -> None:
        if not self.slot.strip():
            raise ValueError("slot must not be empty")
        # New interfaces use price_*; the existing IntentResult dictionaries
        # remain unchanged for legacy consumers.
        object.__setattr__(self, "slot", {"budget_min": "price_min", "budget_max": "price_max"}.get(self.slot, self.slot))
        if self.operation not in {"set", "clear", "exclude", "remove_exclusion"}:
            raise ValueError("unsupported slot operation")
        if not isinstance(self.values, (tuple, list)):
            raise ValueError("values must be a sequence, not a scalar string")
        object.__setattr__(self, "values", tuple(self.values))
        if (self.operation == "clear") != (len(self.values) == 0):
            raise ValueError("clear takes no values; other operations require values")
        if self.operation == "set":
            if self.constraint_type not in {"hard", "soft"}:
                raise ValueError("set requires an explicit hard/soft tier")
        elif self.constraint_type is not None:
            raise ValueError("only set has a hard/soft tier; exclusions apply globally")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between zero and one")
        for value in self.values:
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError("slot values must be scalar")
            if isinstance(value, str) and not value.strip():
                raise ValueError("slot values must not be blank")
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError("slot values must be finite")


@dataclass(frozen=True)
class IntentResult:
    """Stable hand-off contract for retrieval, state, and ranking modules."""

    raw_query: str
    normalized_query: str
    intent_type: IntentType
    intent_confidence: float
    slots: dict[str, list[str] | float]
    hard_constraints: dict[str, list[str] | float]
    filter_constraints: dict[str, list[str] | float]
    soft_preferences: dict[str, list[str] | float]
    keyword_query: str
    semantic_query: str
    ambiguity_flags: list[str] = field(default_factory=list)
    override_detected: bool = False
    decision_evidence: dict[str, object] = field(default_factory=dict)
    # None = producer has not supplied explicit operations; () = explicitly
    # no slot changes. The existing router leaves this as None, not a guess.
    slot_updates: tuple[SlotUpdate, ...] | None = None

    def __post_init__(self) -> None:
        if self.slot_updates is not None:
            if not isinstance(self.slot_updates, (tuple, list)) or any(not isinstance(item, SlotUpdate) for item in self.slot_updates):
                raise ValueError("slot_updates must contain SlotUpdate objects")
            object.__setattr__(self, "slot_updates", tuple(self.slot_updates))

    def to_dict(self) -> dict:
        """Keep the legacy wire shape stable for existing consumers."""
        payload = asdict(self)
        payload.pop("slot_updates")
        return payload

    def to_state_handoff(self, *, session_id: str, turn: int) -> dict:
        """Versioned 1 -> 3 envelope; it does not apply updates to state."""
        if not session_id.strip() or type(turn) is not int or turn < 1:
            raise ValueError("session_id and a positive integer turn are required")
        return {
            "schema_version": "2.0",
            "session_id": session_id,
            "turn": turn,
            "intent": self.intent_type or "unknown",
            "slot_updates": None if self.slot_updates is None else [asdict(item) for item in self.slot_updates],
            "legacy_result": self.to_dict(),
        }
