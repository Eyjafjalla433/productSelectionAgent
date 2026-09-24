"""Adapters from intent-recognition and conversation-state-memory to ranking.

The ranking package intentionally does not re-parse the shopping conversation.
It consumes the sibling modules' stable contracts:

* ``IntentResult`` supplies the current-turn route and query constraints.
* ``ContextSnapshot`` supplies accumulated hard/soft slots, exclusions,
  profile hints, and the compact session summary.
* The official ``reset(user_profile)`` remains the primary long-term profile.
"""

from __future__ import annotations

from typing import Any, Mapping

from techjam_agent.contracts import Requirements


def _flatten_values(values: object) -> list[str]:
    if isinstance(values, Mapping):
        return [str(value) for value in values.keys()]
    if isinstance(values, (list, tuple, set)):
        return [str(value) for value in values]
    return [str(values)]


def snapshot_to_requirements(snapshot: Any) -> Requirements:
    """Convert a ``ContextSnapshot`` into the retrieval-side requirements shape.

    This does not replace the official requirements collector; it gives the
    reranker an independent, structured view of the current conversation state.
    """

    must = getattr(snapshot, "must_match", None) or {}
    should = getattr(snapshot, "should_match", None) or {}
    category = str(must.get("category") or "").strip()
    hard_constraints: list[str] = []
    soft_preferences: list[str] = []
    for name, value in must.items():
        if name == "category":
            continue
        for item in _flatten_values(value):
            cleaned = item.strip()
            if cleaned:
                if name in {"price_min", "price_max"}:
                    budget_op = "at least" if name == "price_min" else "under"
                    soft_preferences.append(f"budget: {budget_op} {cleaned}")
                else:
                    hard_constraints.append(f"{name}: {cleaned}")
    for name, value in should.items():
        for item in _flatten_values(value):
            cleaned = item.strip()
            if cleaned:
                soft_preferences.append(f"{name}: {cleaned}")

    if not category:
        query = str(getattr(snapshot, "query", "") or "").strip()
        category = query or "clothing item"
    return Requirements(
        category=category,
        hard_constraints=tuple(hard_constraints),
        soft_preferences=tuple(soft_preferences),
    )


def intent_to_requirements(intent_result: Any | None) -> Requirements:
    """Convert the current-turn ``IntentResult`` into ranking requirements.

    Intent recognition is the authoritative per-turn parser. State memory
    accumulates history, but the current turn must be able to override earlier
    slots. This adapter therefore uses the fresh ``hard_constraints`` and
    ``soft_preferences`` dictionaries directly instead of re-parsing text.
    """

    if intent_result is None:
        return Requirements("", (), ())
    hard = getattr(intent_result, "hard_constraints", None) or {}
    soft = getattr(intent_result, "soft_preferences", None) or {}
    slots = getattr(intent_result, "slots", None) or {}
    hard_category = hard.get("category")
    category_values = (
        _flatten_values(hard_category)
        if hard_category not in (None, "", [])
        else _flatten_values(slots.get("category"))
    )
    category = category_values[0].strip() if category_values else ""

    hard_constraints: list[str] = []
    soft_preferences: list[str] = []
    for name, value in hard.items():
        if name == "category":
            continue
        for item in _flatten_values(value):
            cleaned = item.strip()
            if not cleaned:
                continue
            if name in {"budget_min", "budget_max"}:
                budget_op = "at least" if name == "budget_min" else "under"
                soft_preferences.append(f"budget: {budget_op} {cleaned}")
            elif name.endswith("_exclude"):
                continue
            else:
                hard_constraints.append(f"{name}: {cleaned}")
    for name, value in soft.items():
        for item in _flatten_values(value):
            cleaned = item.strip()
            if cleaned:
                soft_preferences.append(f"{name}: {cleaned}")
    return Requirements(
        category=category or "clothing item",
        hard_constraints=tuple(hard_constraints),
        soft_preferences=tuple(soft_preferences),
    )


def merge_profile_with_snapshot(
    official_profile: Mapping[str, Any] | None,
    snapshot: Any | None,
) -> dict[str, Any]:
    """Merge official reset profile with conversation-derived profile hints.

    Official ``preference_tags`` and summary win when both are present. State
    memory ``profile_hints`` are appended only as additive tags, and the short
    session summary is attached separately so prompt rendering can include it
    without inflating the long-term profile block.
    """

    profile = dict(official_profile or {})
    tags = [str(value) for value in profile.get("preference_tags") or []]
    if snapshot is not None:
        hints = getattr(snapshot, "profile_hints", None) or {}
        for name, values in hints.items():
            for value in _flatten_values(values):
                tag = f"{name}:{value}"
                if tag not in tags:
                    tags.append(tag)
        session_summary = str(getattr(snapshot, "session_summary", "") or "").strip()
        if session_summary:
            profile["conversation_summary"] = session_summary
    profile["preference_tags"] = tags
    return profile


def intent_to_context(intent_result: Any | None) -> dict[str, Any]:
    """Reduce ``IntentResult`` to only the fields useful for ranking strategy."""

    if intent_result is None:
        return {}
    flags = getattr(intent_result, "ambiguity_flags", None) or []
    return {
        "intent_type": getattr(intent_result, "intent_type", None),
        "intent_confidence": getattr(intent_result, "intent_confidence", 0.0),
        "route": getattr(intent_result, "route", None),
        "route_reason": getattr(intent_result, "route_reason", None),
        "override_detected": bool(getattr(intent_result, "override_detected", False)),
        "ambiguity_flags": [str(value) for value in flags],
        "semantic_query": getattr(intent_result, "semantic_query", ""),
    }


__all__ = [
    "intent_to_context",
    "intent_to_requirements",
    "merge_profile_with_snapshot",
    "snapshot_to_requirements",
]
