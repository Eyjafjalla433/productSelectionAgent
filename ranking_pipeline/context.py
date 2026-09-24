"""Conversation context and profile helpers used by contextual reranking."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from techjam_agent.contracts import Requirements


OVERRIDE_RE = re.compile(
    r"ignore\s+(?:my\s+)?(?:earlier|previous)\s+(?:preference|requirement)"
    r".*?What I need is:\s*(.+?)(?:\.?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProfileFeatures:
    """Safe aggregate profile fields already visible to the Agent."""

    preference_tags: tuple[str, ...]
    summary: str
    average_prior_rating: float | None
    rating_style: str
    purchase_frequency: str


@dataclass(frozen=True)
class ShortTermSummary:
    requirements: Requirements
    clarification_turns: tuple[str, ...]
    override_turns: tuple[int, ...]


def profile_features(profile: Mapping[str, Any] | None) -> ProfileFeatures:
    if not profile:
        return ProfileFeatures((), "", None, "", "")
    tags = profile.get("preference_tags") or []
    rating = profile.get("average_prior_rating")
    return ProfileFeatures(
        preference_tags=tuple(str(value) for value in tags),
        summary=str(profile.get("summary") or ""),
        average_prior_rating=float(rating) if rating not in (None, "") else None,
        rating_style=str(profile.get("rating_style") or ""),
        purchase_frequency=str(profile.get("purchase_frequency") or ""),
    )


def parse_override_message(message: str) -> tuple[str, ...]:
    """Return the replacement requirements from an intent-override message."""

    match = OVERRIDE_RE.search(message)
    if not match:
        return ()
    return tuple(
        value.strip(" .")
        for value in match.group(1).rstrip(".").split(";")
        if value.strip()
    )


def apply_override(requirements: Requirements, message: str) -> Requirements:
    """Replace displaced soft preferences when the customer overrides intent."""

    replacements = parse_override_message(message)
    if not replacements:
        return requirements
    replacement_set = set(replacements)
    return Requirements(
        category=requirements.category,
        hard_constraints=tuple(
            dict.fromkeys((*requirements.hard_constraints, *replacements))
        ),
        soft_preferences=tuple(
            value for value in requirements.soft_preferences if value not in replacement_set
        ),
    )


def compact_profile(profile: Mapping[str, Any] | None) -> str:
    """Render a low-token profile block; the profile is treated as a weak prior."""

    features = profile_features(profile)
    parts = []
    if features.preference_tags:
        parts.append(f"preference_tags: {', '.join(features.preference_tags)}")
    if features.rating_style:
        parts.append(f"rating_style: {features.rating_style}")
    if features.purchase_frequency:
        parts.append(f"purchase_frequency: {features.purchase_frequency}")
    if features.summary:
        parts.append(f"summary: {features.summary}")
    conversation_summary = str(profile.get("conversation_summary") or "") if profile else ""
    if conversation_summary:
        parts.append(f"session: {conversation_summary[:240]}")
    return "; ".join(parts)


def compact_requirements(requirements: Requirements) -> str:
    return (
        f"category: {requirements.category}; "
        f"hard_constraints: {', '.join(requirements.hard_constraints) or 'none'}; "
        f"soft_preferences: {', '.join(requirements.soft_preferences) or 'none'}"
    )
