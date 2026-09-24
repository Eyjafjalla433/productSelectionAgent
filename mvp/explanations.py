"""Target-blind catalog evidence summaries for displayed products."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from shopping_agent.retrieval import category_matches
from techjam_agent.query import parse_text


TEXT_FIELDS = ("title", "features", "description", "details", "categories")


def _flatten(value: object) -> str:
    if isinstance(value, dict):
        return " ".join(f"{key} {_flatten(item)}" for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return " ".join(_flatten(item) for item in value)
    return "" if value is None else str(value)


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _terms(value: object) -> set[str]:
    return {_singular(word) for word in re.findall(r"[a-z0-9]+", _flatten(value).casefold())}


def _values(value: object) -> list[object]:
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _price(product: dict[str, Any]) -> float | None:
    try:
        value = float(str(product.get("price", "")).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _text_match(value: object, corpus: set[str]) -> bool:
    required = _terms(value)
    return bool(required) and required <= corpus


def explain_product(product: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    full_terms = _terms([product.get(field) for field in TEXT_FIELDS])
    brand_terms = _terms([product.get("store"), product.get("details")])
    price = _price(product)
    signals: list[dict[str, Any]] = []

    for slot, raw in receipt.get("hard", {}).items():
        values = _values(raw)
        status = "not_evidenced"
        evidence = "not found in catalog text"
        if slot in {"price_min", "price_max"}:
            if price is None:
                status, evidence = "unknown", "catalog price unavailable"
            else:
                threshold = float(values[0])
                supported = price >= threshold if slot == "price_min" else price <= threshold
                status = "supported" if supported else "conflict"
                operator = ">=" if slot == "price_min" else "<="
                evidence = f"${price:.2f} {operator} ${threshold:.2f}"
        else:
            if slot == "category":
                matches = [category_matches(product, value) for value in values]
                supported = next((source for matched, source in matches if matched), None)
                if supported:
                    status = "supported"
                    evidence = supported
                elif matches:
                    status = "conflict"
                    evidence = matches[0][1]
                signals.append({"tier": "hard", "slot": slot, "value": raw, "status": status, "evidence": evidence})
                continue
            corpus = brand_terms if slot == "brand" else full_terms
            evidence_values = (
                [" ".join(parse_text(str(value)).retrieval_terms) for value in values]
                if slot.startswith("feature_")
                else values
            )
            if any(_text_match(value, corpus) for value in evidence_values):
                status = "supported"
                evidence = "store/details" if slot == "brand" else "catalog text"
        signals.append({"tier": "hard", "slot": slot, "value": raw, "status": status, "evidence": evidence})

    for slot, raw_values in receipt.get("soft", {}).items():
        values = _values(raw_values)
        matched = [value for value in values if _text_match(value, full_terms)]
        signals.append(
            {
                "tier": "soft",
                "slot": slot,
                "value": raw_values,
                "status": "supported" if matched else "not_evidenced",
                "evidence": f"matched: {', '.join(map(str, matched))}" if matched else "not found in catalog text",
            }
        )

    for slot, raw_values in receipt.get("excluded", {}).items():
        values = _values(raw_values)
        conflicts = [value for value in values if _text_match(value, full_terms)]
        signals.append(
            {
                "tier": "excluded",
                "slot": slot,
                "value": raw_values,
                "status": "conflict" if conflicts else "clear",
                "evidence": f"found: {', '.join(map(str, conflicts))}" if conflicts else "no excluded term found",
            }
        )

    hard = [signal for signal in signals if signal["tier"] == "hard"]
    soft = [signal for signal in signals if signal["tier"] == "soft"]
    hard_supported = sum(signal["status"] == "supported" for signal in hard)
    soft_supported = sum(signal["status"] == "supported" for signal in soft)
    return {
        "hard_supported": hard_supported,
        "hard_total": len(hard),
        "soft_supported": soft_supported,
        "soft_total": len(soft),
        "hard_coverage": round(hard_supported / len(hard), 4) if hard else 1.0,
        "signals": signals,
        "disclaimer": "Lexical catalog evidence, not verified variant availability.",
    }


def summarize_explanations(explanations: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(explanations)
    if not rows:
        return {"product_count": 0, "fully_supported_count": 0, "average_hard_coverage": None, "soft_supported_count": 0}
    return {
        "product_count": len(rows),
        "fully_supported_count": sum(row["hard_supported"] == row["hard_total"] for row in rows),
        "average_hard_coverage": round(sum(row["hard_coverage"] for row in rows) / len(rows), 4),
        "soft_supported_count": sum(row["soft_supported"] > 0 for row in rows),
    }


def _catalog_snippets(product: dict[str, Any]) -> list[tuple[str, str]]:
    snippets: list[tuple[str, str]] = []
    for source, field in (("bullet_point", "features"), ("description", "description")):
        values = product.get(field) or ()
        if isinstance(values, str):
            values = (values,)
        for value in values:
            text = " ".join(str(value).split())
            if text:
                snippets.append((source, text[:500]))
    return snippets


def product_advice(product: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    """Build concise, source-labelled fit and watch-out points.

    This is deliberately extractive. Catalog marketing claims remain attributed
    to their source and unsupported or missing requirements remain visible.
    """
    snippets = _catalog_snippets(product)
    pros: list[dict[str, Any]] = []
    cons: list[dict[str, Any]] = []
    used_evidence: set[str] = set()
    for signal in match.get("signals", ()):
        tier = signal.get("tier")
        status = signal.get("status")
        slot = str(signal.get("slot") or "requirement")
        value = signal.get("value")
        if status == "supported" and tier in {"hard", "soft"}:
            evidence_value = (
                " ".join(parse_text(str(value)).retrieval_terms)
                if slot.startswith("feature_")
                else value
            )
            required = _terms(evidence_value)
            source, quote = next(
                ((source, text) for source, text in snippets if required and required <= _terms(text)),
                ("match_check", str(signal.get("evidence") or "catalog evidence")),
            )
            if quote not in used_evidence:
                pros.append({"text": f"Supports {slot}: {_flatten(value)}", "source": source, "evidence": quote})
                used_evidence.add(quote)
        elif tier == "hard" and status in {"conflict", "unknown", "not_evidenced"}:
            label = "Conflicts with" if status == "conflict" else "Could not verify"
            cons.append(
                {
                    "text": f"{label} {slot}: {_flatten(value)}",
                    "source": "match_check",
                    "evidence": str(signal.get("evidence") or "catalog evidence unavailable"),
                }
            )
        elif tier == "excluded" and status == "conflict":
            cons.append(
                {
                    "text": f"Contains excluded {slot}: {_flatten(value)}",
                    "source": "match_check",
                    "evidence": str(signal.get("evidence") or "excluded term found"),
                }
            )

    rating = product.get("average_rating")
    rating_count = product.get("rating_number")
    try:
        numeric_rating = float(rating)
        numeric_count = int(rating_count or 0)
    except (TypeError, ValueError):
        numeric_rating, numeric_count = 0.0, 0
    if numeric_rating >= 4.0 and numeric_count > 0 and len(pros) < 3:
        pros.append(
            {
                "text": f"Catalog rating {numeric_rating:.1f}/5 from {numeric_count:,} ratings",
                "source": "catalog_rating",
                "evidence": f"average_rating={numeric_rating:.1f}; rating_number={numeric_count}",
            }
        )
    if product.get("price") is None and len(cons) < 3:
        cons.append(
            {
                "text": "Price is unavailable in the frozen catalog",
                "source": "catalog_price",
                "evidence": "price=null",
            }
        )
    if not cons:
        cons.append(
            {
                "text": "Variant availability and fit still need seller-page verification",
                "source": "system_limit",
                "evidence": "catalog records do not verify live variants or inventory",
            }
        )
    highlights = [
        {"source": source, "evidence": text}
        for source, text in snippets[:3]
    ]
    return {
        "pros": pros[:3],
        "cons": cons[:3],
        "catalog_highlights": highlights,
        "disclaimer": "Extractive catalog evidence; seller claims and live variant availability are not independently verified.",
    }
