"""Evidence-gated optional model assistance for requirement extraction."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from intent_router.models import SlotUpdate

from .model_provider import ModelProviderError, StructuredModelProvider


ALLOWED_SLOTS = {
    "category", "color", "material", "brand", "size", "style", "use_case",
    "feature", "price_min", "price_max", "budget_min", "budget_max",
}
NEGATION_RE = re.compile(r"\b(?:no|not|without|avoid|exclude)\b|(?:不要|不含|避免|排除)", re.I)
DIRECT_CATEGORY_RE = re.compile(r"\b(?:need|want|looking for|switch to|change to|find me)\b|(?:需要|想要|找|换成|改成)", re.I)

SYSTEM_PROMPT = """You extract shopping requirements as JSON. Never recommend products.
Return exactly {"updates":[{"slot":"...","operation":"set|exclude","values":["..."],"constraint_type":"hard|soft","evidence":"exact quote from the user"}]}.
Allowed slots: category, color, material, brand, size, style, use_case, feature, price_min, price_max.
Every evidence string and every textual value must occur verbatim in the current user message. Use exclude only for an explicitly negated value. Use JSON and no prose. Omit uncertain updates."""


@dataclass(frozen=True)
class EnhancementOutcome:
    updates: tuple[SlotUpdate, ...]
    provider: str | None
    model: str | None
    usage: dict[str, int]
    latency_ms: float
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "updates": [asdict(update) for update in self.updates]}


def _normalized(value: object) -> str:
    # ``[^\W_]`` is the Unicode-aware set of letters and digits. Keeping CJK
    # characters makes the same evidence gate work for Chinese without fuzzy
    # matching or transliteration.
    return " ".join(re.findall(r"[^\W_]+", str(value).casefold(), re.UNICODE))


def _is_grounded(value: object, message: str) -> bool:
    needle = _normalized(value)
    return bool(needle) and needle in _normalized(message)


class RequirementEnhancer:
    def __init__(self, provider: StructuredModelProvider, *, max_updates: int = 6) -> None:
        if not 1 <= max_updates <= 12:
            raise ValueError("max_updates must be between 1 and 12")
        self.provider = provider
        self.max_updates = max_updates

    def enhance(self, message: str, deterministic: Iterable[SlotUpdate]) -> EnhancementOutcome:
        deterministic = tuple(deterministic)
        occupied = {update.slot for update in deterministic if update.operation in {"set", "clear"}}
        user_payload = {
            "current_user_message": message,
            "deterministic_updates": [asdict(update) for update in deterministic],
            "instruction": "Only add missing, directly evidenced requirements. Output JSON.",
        }
        try:
            result = self.provider.complete_json(
                system=SYSTEM_PROMPT,
                user=json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
                max_tokens=700,
            )
        except (ModelProviderError, ValueError, TypeError) as exc:
            return EnhancementOutcome((), self.provider.name, self.provider.model, {"prompt_tokens": 0, "completion_tokens": 0}, 0.0, type(exc).__name__)

        accepted: list[SlotUpdate] = []
        rows = result.data.get("updates", [])
        if not isinstance(rows, list):
            rows = []
        for row in rows[: self.max_updates]:
            if not isinstance(row, dict):
                continue
            slot = str(row.get("slot") or "").strip()
            slot = {"budget_min": "price_min", "budget_max": "price_max"}.get(slot, slot)
            operation = str(row.get("operation") or "").strip()
            values = row.get("values")
            evidence = str(row.get("evidence") or "").strip()
            if slot not in ALLOWED_SLOTS or slot in occupied or operation not in {"set", "exclude"}:
                continue
            if not isinstance(values, list) or not 1 <= len(values) <= 4:
                continue
            if not evidence or evidence.casefold() not in message.casefold():
                continue
            if not all(isinstance(value, (str, int, float)) and _is_grounded(value, message) for value in values):
                continue
            if operation == "exclude":
                if not NEGATION_RE.search(evidence):
                    continue
                accepted.append(SlotUpdate(slot, "exclude", tuple(values), confidence=0.7, evidence=evidence))
                continue
            tier = "soft"
            if slot == "category" and DIRECT_CATEGORY_RE.search(evidence):
                tier = "hard"
            elif slot in {"price_min", "price_max"}:
                tier = "hard"
            accepted.append(SlotUpdate(slot, "set", tuple(values), tier, 0.7, evidence))
            occupied.add(slot)
        warning = None if accepted else "no_grounded_updates"
        return EnhancementOutcome(tuple(accepted), result.provider, result.model, result.usage, result.latency_ms, warning)
