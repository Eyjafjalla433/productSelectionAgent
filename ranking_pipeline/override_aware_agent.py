"""Experimental override-aware branch for intent_override ablation.

This module intentionally leaves ``ranking_pipeline/agent.py`` unchanged. It
uses the same Top50 generator and locked RRF reranker as the locked-exact
submission, but parses the first turn more completely and applies slot-level
replacement when an intent override is detected.

The override resolver is conservative: it first removes preferences that share
the same semantic slot as the replacement or are explicitly negated. Only when
the message is a generic ``ignore my earlier preference`` does it fall back to
the most recently recorded soft preference.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from techjam_agent.agent import Agent as BaseAgent
from techjam_agent.dialogue import RequirementsCollector as BaseRequirementsCollector
from techjam_agent.ranking import LockedWeightedRrfTop10Reranker

from ranking_pipeline.context import ShortTermSummary, parse_override_message


INITIAL_OVERRIDE_RE = re.compile(
    r"^I'm looking for\s+(.+?)\.(?:\s+(.+?))?\s*$",
    re.IGNORECASE,
)
OTHER_PREFIX = "For that, what matters is: "
OVERRIDE_CUE_RE = re.compile(
    r"\b(actually|instead|ignore\s+(?:my\s+)?(?:earlier|previous)|change\s+my\s+mind)\b",
    re.IGNORECASE,
)
GENERIC_ANAPHORA_RE = re.compile(
    r"\bignore\s+(?:my\s+)?(?:earlier|previous)\b",
    re.IGNORECASE,
)
NEGATION_RE = re.compile(
    r"\b(?:no|not|without|avoid|don't want|do not want)\s+(?:a\s+|an\s+|any\s+)?([a-z][a-z -]{1,40}?)(?=\s+(?:and|but|for|with)\b|[,.;]|$)",
    re.IGNORECASE,
)

_COLORS = {
    "black", "white", "blue", "red", "pink", "green", "brown", "gray", "grey",
    "purple", "yellow", "orange", "beige", "navy", "gold", "silver",
}
_MATERIALS = {
    "cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk",
    "rayon", "linen", "denim", "suede", "cashmere", "fabric",
}
_SIZES = {"xxs", "xs", "s", "m", "l", "xl", "xxl", "xxxl", "small", "medium", "large", "extra large"}
_STYLES = {
    "casual", "formal", "vintage", "athletic", "classic", "bohemian",
    "minimalist", "slim fit", "regular fit", "relaxed fit", "crew neck",
    "v-neck", "long sleeve", "short sleeve", "high waisted", "buckle closure",
}
_USE_CASES = {
    "running", "walking", "hiking", "gym", "work", "office", "wedding", "party",
    "travel", "outdoor", "winter", "summer", "everyday",
}


def _slot_type(value: str) -> str:
    normalized = " ".join(str(value).strip().lower().split())
    if not normalized:
        return "unknown"
    if normalized in _MATERIALS:
        return "material"
    if normalized in _COLORS:
        return "color"
    if normalized in _SIZES or re.search(r"\b(?:size|us)\s*\d", normalized):
        return "size"
    if normalized in _STYLES:
        return "style"
    if normalized in _USE_CASES:
        return "use_case"
    if re.search(r"\b(?:under|below|over|around|budget)\b|\$\d", normalized):
        return "budget"
    return "unknown"


def _normalized(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


@dataclass
class OverrideAwareRequirementsCollector(BaseRequirementsCollector):
    """Collector that understands the override-style initial message."""

    user_profile: dict = field(default_factory=dict)
    override_turns: list[int] = field(default_factory=list)
    soft_disclosed_order: list[str] = field(default_factory=list)

    def observe(self, user_message: str, turn: int) -> None:
        if turn == 1:
            super().observe(user_message, turn)
            if not self.category and self._looks_like_override_initial(user_message):
                self._observe_override_initial(user_message)
            return

        if OVERRIDE_CUE_RE.search(user_message) or parse_override_message(user_message):
            self._apply_override(user_message, turn)
            return

        before_soft = list(self.soft_preferences)
        super().observe(user_message, turn)
        for value in self.soft_preferences:
            if value not in before_soft:
                self._record_soft(value)

    def _looks_like_override_initial(self, message: str) -> bool:
        match = INITIAL_OVERRIDE_RE.search(message)
        return bool(match and match.group(2))

    def _observe_override_initial(self, message: str) -> None:
        match = INITIAL_OVERRIDE_RE.search(message)
        if not match:
            return
        self.category = match.group(1).strip()
        rest = (match.group(2) or "").strip(" .")
        if rest:
            for value in rest.split(";"):
                cleaned = value.strip(" .")
                if cleaned:
                    self._append_unique(self.soft_preferences, cleaned)
                    self._record_soft(cleaned)

    def _parse_replacements(self, message: str) -> list[str]:
        replacements = list(parse_override_message(message))
        if replacements:
            return replacements
        match = re.search(
            r"(?:What I need is|I need)\s*:?\s*(.+?)\.?\s*$",
            message,
            re.IGNORECASE,
        )
        if not match:
            return []
        return [
            value.strip(" .")
            for value in match.group(1).rstrip(".").split(";")
            if value.strip()
        ]

    def _apply_override(self, message: str, turn: int) -> None:
        replacements = self._parse_replacements(message)
        new_items = [_normalized(value) for value in replacements]
        new_slots = {_slot_type(value) for value in new_items if _slot_type(value) != "unknown"}

        remove_indices: list[int] = []
        for index, value in enumerate(self.soft_preferences):
            normalized = _normalized(value)
            slot = _slot_type(value)
            if normalized in new_items:
                remove_indices.append(index)
                continue
            if slot != "unknown" and slot in new_slots:
                remove_indices.append(index)

        for match in NEGATION_RE.finditer(message):
            negated = _normalized(match.group(1))
            for index, value in enumerate(self.soft_preferences):
                if index in remove_indices:
                    continue
                if negated in _normalized(value) or _normalized(value) in negated:
                    remove_indices.append(index)

        if not remove_indices and self.soft_preferences and GENERIC_ANAPHORA_RE.search(message):
            fallback_index = self._last_disclosed_soft_index()
            if fallback_index is not None:
                remove_indices.append(fallback_index)

        for index in sorted(set(remove_indices), reverse=True):
            if 0 <= index < len(self.soft_preferences):
                removed_value = self.soft_preferences.pop(index)
                if removed_value in self.soft_disclosed_order:
                    self.soft_disclosed_order.remove(removed_value)

        for value in replacements:
            if value and value not in self.hard_constraints:
                self.hard_constraints.append(value)
        self.override_turns.append(turn)

    def _record_soft(self, value: str) -> None:
        if value and value not in self.soft_disclosed_order:
            self.soft_disclosed_order.append(value)

    def _last_disclosed_soft_index(self) -> int | None:
        for value in reversed(self.soft_disclosed_order):
            try:
                return self.soft_preferences.index(value)
            except ValueError:
                continue
        return None

    def short_term_summary(self) -> ShortTermSummary:
        return ShortTermSummary(
            requirements=self.requirements(),
            clarification_turns=tuple(str(index) for index in range(self.other_reply_count)),
            override_turns=tuple(self.override_turns),
        )

    @staticmethod
    def _append_unique(target: list[str], value: str) -> None:
        if value and value not in target:
            target.append(value)


class OverrideAwareAgent(BaseAgent):
    """Locked-exact agent with the override-aware collector swapped in."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        retrieval_mode: str = "exact",
    ) -> None:
        from techjam_agent.retrieval import (
            ExactDenseTop50CandidateGenerator,
            LiteTop50CandidateGenerator,
        )

        if retrieval_mode == "exact":
            candidate_generator = ExactDenseTop50CandidateGenerator(catalog_path)
        elif retrieval_mode == "lite":
            candidate_generator = LiteTop50CandidateGenerator(catalog_path)
        else:
            raise ValueError("retrieval_mode must be 'exact' or 'lite'")
        super().__init__(
            catalog_path,
            candidate_generator=candidate_generator,
            reranker=LockedWeightedRrfTop10Reranker(),
        )

    def reset(self, session_id: str, user_profile: dict) -> None:
        self._sessions[session_id] = OverrideAwareRequirementsCollector(
            user_profile=user_profile
        )

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        collector = self._sessions.get(session_id)
        if collector is None:
            raise RuntimeError("reset must be called before respond")
        collector.observe(user_message, turn)
        if turn <= 2:
            return {
                "message": "Please share any other requirements that matter.",
                "ask_attribute": "other",
                "recommendations": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }
        candidate_set = self.candidate_generator.generate(
            collector.requirements(), session_id=session_id, turn=turn
        )
        result = self.reranker.rerank(candidate_set, top_k=top_k)
        result.validate_against(candidate_set, top_k=top_k)
        return {
            "message": "Here are the best matches for all requirements you shared.",
            "ask_attribute": None,
            "recommendations": [
                {"parent_asin": candidate.parent_asin}
                for candidate in result.ranked_candidates
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }


__all__ = ["OverrideAwareAgent", "OverrideAwareRequirementsCollector"]
