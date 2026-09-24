"""Target-blind, non-operative question diagnostics for the product demo.

This module does not choose the scored Agent's ``ask_attribute``. It estimates
which catalog facet would best divide the candidates already returned by the
real retrieval stage, so a reviewer can compare product-facing and benchmark
policies without silently changing either one.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Iterable


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
    "slim fit", "loose fit", "oversized", "sleeveless", "long sleeve",
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


def _first_phrase(text: str, vocabulary: Iterable[str]) -> str:
    for phrase in vocabulary:
        if re.search(rf"\b{re.escape(phrase)}\b", text):
            return "gray" if phrase == "grey" else phrase
    return ""


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
        "material": _first_phrase(text, MATERIALS),
        "color": _first_phrase(text, COLORS),
        "style": _first_phrase(text, STYLES),
        "use_case": _first_phrase(text, USE_CASES),
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
    turns_left: int = 0,
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
    cost = 1.0 / (max(0, int(turns_left)) + 1.0)
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
