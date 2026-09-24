"""Target-blind candidate question scoring shared by diagnostics and policy.

This module does not choose the scored Agent's ``ask_attribute``. It estimates
which catalog facet would best divide the candidates already returned by the
real retrieval stage, so a reviewer can compare product-facing and benchmark
policies. Product conversational mode also uses these evidence scores, with
explicit coverage thresholds and escape conditions in shopping_agent.policy.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable
from shopping_agent.retrieval import product_colors, style_matches


MATERIALS = (
    "cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk",
    "rayon", "denim", "linen", "suede", "fleece", "canvas", "mesh",
)
COLORS = (
    "black", "white", "blue", "red", "pink", "green", "brown", "gray",
    "grey", "purple", "yellow", "orange", "beige", "navy", "tan",
)
STYLES = (
    "casual", "formal", "athletic", "vintage", "classic", "elegant",
    "slim fit", "loose fit", "regular fit", "sleeveless", "long sleeve",
)
USE_CASES = (
    "hiking", "running", "walking", "gym", "winter", "outdoor", "work",
    "wedding", "party", "beach", "travel", "yoga", "workout", "summer",
)


def _text(product: dict[str, Any]) -> str:
    values: list[str] = []
    for field in ("title", "features", "details", "description", "categories"):
        value = product.get(field)
        if isinstance(value, dict):
            values.extend(f"{key} {item}" for key, item in value.items())
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value is not None:
            values.append(str(value))
    return " ".join(values).casefold()


def _single_phrase(text: str, vocabulary: Iterable[str]) -> str:
    """Overlapping/contradictory facets cannot pretend to be disjoint groups."""
    values = set()
    for phrase in vocabulary:
        for match in re.finditer(rf'\b{re.escape(phrase)}\b', text):
            if not re.search(r'\b(?:not|no|without)\s*$', text[:match.start()]):
                values.add(phrase)
    return next(iter(values)) if len(values) == 1 else ''


def _single_color(product):
    values = product_colors(product)
    # Navy belongs to the blue family; do not count the same item twice.
    values.discard('navy')
    return next(iter(values)) if len(values) == 1 else ''


def _single_style(product):
    # Share filtering semantics, including structured Fit Type precedence,
    # fitted/relaxed aliases and negation. Oversized is already loose fit;
    # counting both would make one product look like two distinct groups.
    values = {value for value in STYLES if style_matches(product, value)}
    return next(iter(values)) if len(values) == 1 else ''


def _price_band(value: object) -> str:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(price) or price < 0:
        return ""
    if price < 25:
        return "under $25"
    if price < 50:
        return "$25-49"
    if price < 100:
        return "$50-99"
    return "$100+"


def product_facets(product: dict[str, Any]) -> dict[str, str]:
    text = _text(product)
    categories = product.get("categories") or []
    return {
        "category": str(categories[-1]).strip().casefold() if categories else "",
        "brand": str(product.get("store") or "").strip().casefold(),
        "budget": _price_band(product.get("price")),
        "material": _single_phrase(text, MATERIALS),
        "color": _single_color(product),
        "style": _single_style(product),
        "use_case": _single_phrase(text, USE_CASES),
    }


def option_prompt(attribute: str, value: str) -> str:
    """Create a reviewable prompt that the deterministic router can parse."""
    if attribute == "category":
        return f"I'm looking for {value}."
    if attribute == "brand":
        return f"I prefer the brand {value}."
    if attribute == "use_case":
        return f"It's for {value}."
    if attribute == "budget":
        if value.startswith("under "):
            return f"Keep it {value}."
        match = re.fullmatch(r"\$(\d+)-(\d+)", value)
        if match:
            return f"My budget is between ${match.group(1)} and ${match.group(2)}."
        match = re.fullmatch(r"\$(\d+)\+", value)
        if match:
            return f"My budget is at least ${match.group(1)}."
    return f"I prefer {value} for {attribute.replace('_', ' ')}."


def shadow_question_board(
    products: Iterable[dict[str, Any]],
    *,
    already_known: Iterable[str] = (),
    already_asked: Iterable[str] = (),
    turns_left: int | None = 0,
    max_options: int = 3,
) -> list[dict[str, Any]]:
    """Rank candidate-grounded questions by answerable set reduction.

    Unknown/unoffered values stay in one remainder group. This prevents a
    high-cardinality but practically unanswerable facet such as brand from
    appearing valuable merely because it has many distinct catalog values.
    """
    rows = [product_facets(product) for product in products]
    total = len(rows)
    if total < 2:
        return []
    blocked = {str(value).casefold() for value in (*already_known, *already_asked)}
    # Product mode has no arbitrary remaining-turn countdown. Keep a modest
    # fixed interaction cost instead of pretending user attention is free.
    cost = 0.2 if turns_left is None else 1.0 / (max(0, int(turns_left)) + 1.0)
    board: list[dict[str, Any]] = []
    for attribute in ("category", "material", "color", "style", "use_case", "budget", "brand"):
        if attribute in blocked:
            continue
        counts = Counter(row.get(attribute) or "" for row in rows)
        known = [(value, count) for value, count in counts.items() if value]
        if len(known) < 2:
            continue
        options = sorted(known, key=lambda item: (-item[1], item[0]))[:max_options]
        offered = sum(count for _, count in options)
        remainder = total - offered
        group_sizes = [count for _, count in options]
        if remainder:
            group_sizes.append(remainder)
        expected_remaining = sum(size * size for size in group_sizes) / total
        reduction = max(0.0, 1.0 - expected_remaining / total)
        answerability = offered / total
        net_value = answerability * reduction - cost
        board.append(
            {
                "attribute": attribute,
                "options": [
                    {"value": value, "count": count, "prompt": option_prompt(attribute, value)}
                    for value, count in options
                ],
                "coverage": round(answerability, 4),
                "expected_reduction": round(reduction, 4),
                "interaction_cost": round(cost, 4),
                "net_value": round(net_value, 4),
                "would_ask": net_value > 0,
            }
        )
    return sorted(board, key=lambda row: (-row["net_value"], row["attribute"]))
