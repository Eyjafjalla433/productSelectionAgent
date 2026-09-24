"""Parse official evaluator dialogue into disclosed requirements."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from techjam_agent.contracts import Requirements


CATEGORY_RE = re.compile(r"^I'm looking for (.+?)(?:\. A key requirement is:|, but|\.$)")
INITIAL_HARD_RE = re.compile(r"A key requirement is:\s*(.+?)\.?$")
OTHER_PREFIX = "For that, what matters is: "


def _append_unique(target: list[str], values: list[str]) -> None:
    for value in values:
        cleaned = value.strip(" .")
        if cleaned and cleaned not in target:
            target.append(cleaned)


@dataclass
class RequirementsCollector:
    category: str = ""
    hard_constraints: list[str] = field(default_factory=list)
    soft_preferences: list[str] = field(default_factory=list)
    mode: str = "browsing"
    other_reply_count: int = 0

    def observe(self, user_message: str, turn: int) -> None:
        if turn == 1:
            category_match = CATEGORY_RE.search(user_message)
            if category_match:
                self.category = category_match.group(1).strip()
            hard_match = INITIAL_HARD_RE.search(user_message)
            if hard_match:
                self.mode = "buying"
                _append_unique(self.hard_constraints, [hard_match.group(1)])
            return
        if not user_message.startswith(OTHER_PREFIX):
            return
        values = [
            value.strip()
            for value in user_message[len(OTHER_PREFIX) :].rstrip(".").split(";")
            if value.strip()
        ]
        if self.mode == "browsing" and self.other_reply_count == 0:
            _append_unique(self.hard_constraints, values)
        elif self.mode == "buying" and self.other_reply_count == 0:
            remaining_hard = max(0, 2 - len(self.hard_constraints))
            _append_unique(self.hard_constraints, values[:remaining_hard])
            _append_unique(self.soft_preferences, values[remaining_hard:])
        else:
            _append_unique(self.soft_preferences, values)
        self.other_reply_count += 1

    def requirements(self) -> Requirements:
        return Requirements(
            category=self.category,
            hard_constraints=tuple(self.hard_constraints),
            soft_preferences=tuple(self.soft_preferences),
        )
