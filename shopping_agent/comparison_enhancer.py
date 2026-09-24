"""Evidence-gated model drafts for B's product-description comparison."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .model_provider import ModelProviderError, StructuredModelProvider


SYSTEM_PROMPT = """You draft concise shopping comparison points from catalog text.
Return exactly {"products":[{"parent_asin":"...","pros":[{"text":"...","evidence":"exact catalog quote"}],"cons":[{"text":"...","evidence":"exact catalog quote"}]}]}.
Use only supplied products. Every evidence value must be an exact quote from that product's supplied title, description, bullet points, details, price, or rating fields. Return at most 3 pros and 3 cons per product. Missing facts are not negative claims. Never change rank, score, requirements, or product IDs. JSON only, no prose."""


@dataclass(frozen=True)
class ComparisonOutcome:
    products: tuple[dict[str, Any], ...]
    provider: str | None
    model: str | None
    usage: dict[str, int]
    latency_ms: float
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "products": [dict(row) for row in self.products]}


def _source_texts(product: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = [str(product.get("title") or "")]
    for key in ("product_description", "product_bullet_points"):
        field = product.get(key) or ()
        values.extend(str(value) for value in field)
    details = product.get("details") or {}
    if isinstance(details, dict):
        values.extend(f"{key}: {value}" for key, value in details.items())
        values.extend(str(value) for value in details.values())
    if product.get("price") is not None:
        values.extend((str(product["price"]), f"price={product['price']}"))
    if product.get("average_rating") is not None:
        values.extend((str(product["average_rating"]), f"average_rating={product['average_rating']}"))
    return tuple(" ".join(value.split()) for value in values if value.strip())


def _grounded(evidence: str, sources: tuple[str, ...]) -> bool:
    quote = " ".join(evidence.split()).casefold()
    return len(quote) >= 4 and any(quote in source.casefold() for source in sources)


class ComparisonEnhancer:
    def __init__(self, provider: StructuredModelProvider) -> None:
        self.provider = provider

    def compare(self, handoff: dict[str, Any]) -> ComparisonOutcome:
        products = handoff.get("selected_products") or ()
        if not products:
            return ComparisonOutcome((), self.provider.name, self.provider.model, {"prompt_tokens": 0, "completion_tokens": 0}, 0.0, "no_selected_products")
        bounded = [
            {
                key: product.get(key)
                for key in (
                    "parent_asin", "title", "price", "average_rating", "categories",
                    "product_description", "product_bullet_points", "details", "requirement_match",
                )
            }
            for product in products[:3]
        ]
        payload = {
            "requirements": handoff.get("requirements", {}),
            "products": bounded,
            "instruction": "Draft evidence-grounded pros and cons for each product. Output JSON.",
        }
        try:
            result = self.provider.complete_json(
                system=SYSTEM_PROMPT,
                user=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                max_tokens=1200,
            )
        except (ModelProviderError, ValueError, TypeError) as exc:
            return ComparisonOutcome((), self.provider.name, self.provider.model, {"prompt_tokens": 0, "completion_tokens": 0}, 0.0, type(exc).__name__)

        by_asin = {str(product.get("parent_asin")): product for product in bounded}
        accepted: list[dict[str, Any]] = []
        rows = result.data.get("products", [])
        if not isinstance(rows, list):
            rows = []
        for row in rows[:3]:
            if not isinstance(row, dict):
                continue
            parent_asin = str(row.get("parent_asin") or "")
            product = by_asin.get(parent_asin)
            if product is None:
                continue
            sources = _source_texts(product)
            output: dict[str, Any] = {"parent_asin": parent_asin, "pros": [], "cons": []}
            for field in ("pros", "cons"):
                points = row.get(field) or ()
                if not isinstance(points, list):
                    continue
                for point in points[:3]:
                    if not isinstance(point, dict):
                        continue
                    text = " ".join(str(point.get("text") or "").split())[:240]
                    evidence = " ".join(str(point.get("evidence") or "").split())[:500]
                    if text and _grounded(evidence, sources):
                        output[field].append({"text": text, "evidence": evidence, "source": "model_catalog_quote"})
            if output["pros"] or output["cons"]:
                accepted.append(output)
        warning = None if accepted else "no_grounded_comparison_points"
        return ComparisonOutcome(tuple(accepted), result.provider, result.model, result.usage, result.latency_ms, warning)
