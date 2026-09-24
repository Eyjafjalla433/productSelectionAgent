"""Compact JSON prompt and output parsing for the final reranking LLM."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from techjam_agent.contracts import Candidate, Requirements
from ranking_pipeline.context import compact_profile, compact_requirements


PRODUCT_FIELDS = ("title", "categories", "features", "details", "store", "description")
MAX_CANDIDATE_CHARS = 180


def estimate_prompt_tokens(text: str) -> int:
    """Return a dependency-free approximation of the prompt token count.

    The reranker does not always have access to the live tokenizer at prompt
    build time, so this is intentionally a heuristic for latency and token
    budget telemetry rather than an exact count. CJK characters are counted
    close to one token each; the remaining characters are folded by the
    common four-characters-per-token approximation.
    """

    if not text:
        return 0
    cjk_chars = len(re.findall(r"[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef]", text))
    other_chars = max(0, len(text) - cjk_chars)
    return max(1, int(math.ceil(cjk_chars + (other_chars / 4.0))))


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(str(item) for item in value)
    return str(value)


def candidate_snapshot(candidate: Candidate) -> str:
    product = candidate.product
    parts: list[str] = []
    for field in PRODUCT_FIELDS:
        value = product.get(field)
        if value not in (None, "", [], {}):
            parts.append(_text(value))
    text = " | ".join(parts)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_CANDIDATE_CHARS:
        text = text[: MAX_CANDIDATE_CHARS - 1].rstrip() + "..."
    return f"{candidate.parent_asin} rank={candidate.candidate_rank}: {text}"


@dataclass(frozen=True)
class LLMRankResult:
    ranked_ids: tuple[str, ...]
    constraint_conflicts: tuple[str, ...]
    confidence: float
    need_clarification: bool
    scores: tuple[float, ...] = ()
    reasons: tuple[str, ...] = ()


def build_rerank_prompt(
    requirements: Requirements,
    candidates: Iterable[Candidate],
    *,
    top_k: int,
    user_profile: Mapping[str, Any] | None = None,
) -> str:
    """Build a compact listwise prompt for a small local instruction model."""

    candidate_rows = "\n".join(candidate_snapshot(item) for item in candidates)
    profile = compact_profile(user_profile)
    return (
        "You are a shopping recommendation reranker.\n"
        "Rank only the provided products. Do not invent product IDs. "
        "Preserve hard constraints, treat the profile as a weak prior, and "
        "return JSON only.\n\n"
        f"Requirements: {compact_requirements(requirements)}\n"
        f"Profile: {profile or 'none'}\n"
        "Return exactly the top product IDs in ranked_ids. Include a parallel "
        "scores array and a short reason for each selected ID.\n"
        "Candidates:\n"
        f"{candidate_rows}\n\n"
        '{"ranked_ids":["ID1","ID2"],"scores":[0.9,0.7],"reasons":["strong match","good match"],'
        '"constraint_conflicts":[],"confidence":0.9,"need_clarification":false}'
    )


def _json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON object was not found")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("LLM output is not a JSON object")
    return value


def parse_rerank_output(
    text: str,
    *,
    allowed_ids: Iterable[str],
    top_k: int,
) -> LLMRankResult:
    """Parse strict JSON output and return only valid candidate IDs."""

    allowed = list(allowed_ids)
    allowed_set = set(allowed)
    value = _json_object(text)
    ranked = value.get("ranked_ids")
    if not isinstance(ranked, list):
        raise ValueError("ranked_ids must be a list")
    unique_ids: list[str] = []
    for item in ranked:
        candidate_id = str(item).strip()
        if candidate_id not in allowed_set or candidate_id in unique_ids:
            continue
        unique_ids.append(candidate_id)
        if len(unique_ids) >= top_k:
            break
    if not unique_ids:
        raise ValueError("no valid ranked_ids")
    conflicts = value.get("constraint_conflicts") or []
    if not isinstance(conflicts, list):
        conflicts = []
    confidence = value.get("confidence")
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError):
        confidence_value = 0.0
    confidence_value = max(0.0, min(1.0, confidence_value))
    scores = _parse_parallel_values(value.get("scores"), unique_ids)
    reasons = _parse_parallel_reasons(value.get("reasons"), unique_ids)
    return LLMRankResult(
        ranked_ids=tuple(unique_ids),
        constraint_conflicts=tuple(str(item) for item in conflicts),
        confidence=confidence_value,
        need_clarification=bool(value.get("need_clarification")),
        scores=scores,
        reasons=reasons,
    )


def _parse_parallel_values(raw: Any, ranked_ids: list[str]) -> tuple[float, ...]:
    if isinstance(raw, dict):
        values: list[float] = []
        for candidate_id in ranked_ids:
            try:
                values.append(max(0.0, min(1.0, float(raw.get(candidate_id, 0.0)))))
            except (TypeError, ValueError):
                values.append(0.0)
        return tuple(values)
    if not isinstance(raw, list):
        return ()
    values = []
    for item in raw:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        values.append(max(0.0, min(1.0, value)))
        if len(values) >= len(ranked_ids):
            break
    return tuple(values)


def _parse_parallel_reasons(raw: Any, ranked_ids: list[str]) -> tuple[str, ...]:
    if isinstance(raw, dict):
        return tuple(str(raw.get(candidate_id, "")).strip() for candidate_id in ranked_ids)
    if not isinstance(raw, list):
        return ()
    values = [str(item).strip() for item in raw]
    return tuple((values + [""] * len(ranked_ids))[: len(ranked_ids)])
