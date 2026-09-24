from __future__ import annotations

import json
from pathlib import Path

NON_BRAND_STORE_WORDS = {
    "and", "canvas", "fashion", "for", "key", "men", "new", "on", "sole", "the", "women", "with",
}


def load_catalog_brands(catalog_path: str | Path) -> set[str]:
    """Extract normalized store names for optional brand slot matching."""

    brands: set[str] = set()
    with Path(catalog_path).open(encoding="utf-8") as handle:
        for line in handle:
            product = json.loads(line)
            store = str(product.get("store") or "").strip().lower()
            if len(store) >= 3 and store not in NON_BRAND_STORE_WORDS:
                brands.add(store)
    return brands


def load_catalog_categories(catalog_path: str | Path) -> set[str]:
    """Return catalog-backed category labels suitable for query matching.

    The catalog taxonomy is noisy at higher levels, so only the final two levels
    are retained. Generic department labels such as ``Women`` are excluded.
    """

    ignored = {"clothing", "clothing shoes jewelry", "women", "men", "girls", "boys"}
    categories: set[str] = set()
    with Path(catalog_path).open(encoding="utf-8") as handle:
        for line in handle:
            product = json.loads(line)
            values = [str(value).strip().lower() for value in product.get("categories") or []]
            for value in values[-2:]:
                normalized = " ".join(value.replace("&", " ").replace("-", " ").split())
                if 2 <= len(normalized) <= 40 and normalized not in ignored:
                    categories.add(normalized)
    return categories
