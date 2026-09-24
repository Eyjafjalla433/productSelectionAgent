"""Catalog-derived vocabulary used by the deterministic conversation parser."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).lower()).strip()


def message_ngrams(text: str, maximum_words: int = 5) -> set[str]:
    words = re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)?", text.lower())
    return {
        " ".join(words[start : start + width])
        for start in range(len(words))
        for width in range(1, min(maximum_words, len(words) - start) + 1)
    }


@dataclass(frozen=True)
class CatalogLexicon:
    """Normalized category, brand, and catalog-present feature vocabulary."""

    categories: dict[str, str]
    brands: dict[str, str]
    feature_terms: frozenset[str]
    attribute_values: dict[str, dict[str, str]]

    @classmethod
    def from_jsonl(
        cls,
        catalog_path: str | Path,
        cache_path: str | Path | None = None,
    ) -> "CatalogLexicon":
        resolved_path = Path(catalog_path).resolve()
        stat = resolved_path.stat()
        resolved_cache = Path(cache_path).resolve() if cache_path is not None else _default_cache_path()
        return _load_catalog_lexicon(
            str(resolved_path),
            stat.st_size,
            stat.st_mtime_ns,
            str(resolved_cache),
        )

    def match_categories(self, text: str) -> list[str]:
        matches = self._matches(self.categories, text)
        return [self.categories[term] for term in matches]

    def match_brands(self, text: str) -> list[str]:
        matches = self._matches(self.brands, text)
        # Some catalog ``store`` values are generic product words (for
        # example, "Waterproof").  They are not reliable brand evidence.
        matches = [term for term in matches if term not in self.feature_terms and term not in _GENERIC_BRAND_TERMS]
        return [self.brands[term] for term in matches]

    def match_features(self, text: str) -> list[str]:
        ngrams = message_ngrams(text)
        return sorted((term for term in self.feature_terms if term in ngrams), key=lambda item: (-len(item), item))

    def match_attributes(self, text: str) -> list[tuple[str, str]]:
        """Return catalog-backed (slot name, value) pairs present in a message."""

        ngrams = message_ngrams(text)
        matches = [
            (slot, value, term)
            for slot, values in self.attribute_values.items()
            for term, value in values.items()
            if term in ngrams
        ]
        matches.sort(key=lambda item: (-len(item[2]), item[0], item[2]))
        # Each slot is singular in SessionState; the longest direct catalog
        # value is the least ambiguous match for that slot.
        selected: dict[str, str] = {}
        for slot, value, _ in matches:
            selected.setdefault(slot, value)
        return list(selected.items())

    @staticmethod
    def _matches(vocabulary: dict[str, str], text: str) -> list[str]:
        ngrams = message_ngrams(text)
        return sorted(
            (term for term in vocabulary if term in ngrams),
            key=lambda item: (-len(item), item),
        )


# These terms are only enabled when they are actually present in the supplied
# catalog.  This prevents a generic language rule from inventing a product
# capability that the catalog cannot express.
FEATURE_CANDIDATES = frozenset(
    {
        "adjustable", "arch support", "breathable", "comfortable", "insulated",
        "lightweight", "machine washable", "non slip", "pockets", "quick dry",
        "reflective", "slip resistant", "stretch", "stretchy", "thermal",
        "water resistant", "waterproof", "wide width", "zipper",
    }
)
_GENERIC_CATEGORIES = {"clothing", "clothing shoes jewelry", "clothing shoes and jewelry", "women", "men", "girls", "boys"}
_GENERIC_BRAND_TERMS = _GENERIC_CATEGORIES | {
    "accessories", "apparel", "bag", "bags", "dress", "dresses", "fashion",
    "jewelry", "shoe", "shoes", "style", "women's", "mens", "men's",
}
_DETAIL_SLOT_RULES = (
    ("department", "gender"),
    ("target gender", "gender"),
    ("suggested users", "gender"),
    ("target audience", "gender"),
    ("color", "color"),
    ("fabric", "material"),
    ("material", "material"),
    ("closure", "closure"),
    ("clasp", "closure"),
    ("neck", "neckline"),
    ("collar", "neckline"),
    ("sleeve", "sleeve"),
    ("fit", "fit"),
    ("pattern", "pattern"),
    ("style", "style"),
    ("occasion", "occasion"),
    ("sport", "sport"),
    ("season", "season"),
    ("care", "care"),
    ("sole", "sole_material"),
    ("outer material", "outer_material"),
    ("inner material", "inner_material"),
    ("heel", "heel"),
    ("toe", "toe"),
    ("width", "width"),
    ("special feature", "feature"),
    ("special features", "feature"),
)
_SOFT_ATTRIBUTE_SLOTS = {"fit", "pattern", "style", "occasion", "sport", "season", "care"}
_IGNORED_DETAIL_VALUES = {"", "no", "yes", "n/a", "not applicable", "imported", "unknown"}
_CACHE_SCHEMA_VERSION = 2


def _default_cache_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".cache" / "catalog_lexicon.json"


def _cache_metadata(catalog_path: str, size: int, modified_ns: int) -> dict[str, object]:
    return {
        "schema_version": _CACHE_SCHEMA_VERSION,
        # Do not store an absolute path: the checked-in cache must be usable
        # after a teammate clones the repository into a different directory.
        "catalog_name": Path(catalog_path).name,
        "catalog_size": size,
        "catalog_modified_ns": modified_ns,
    }


def _read_disk_cache(cache_path: Path, metadata: dict[str, object]) -> CatalogLexicon | None:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if payload.get("metadata") != metadata:
            return None
        lexicon = payload["lexicon"]
        return CatalogLexicon(
            categories=dict(lexicon["categories"]),
            brands=dict(lexicon["brands"]),
            feature_terms=frozenset(lexicon["feature_terms"]),
            attribute_values={name: dict(values) for name, values in lexicon["attribute_values"].items()},
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _write_disk_cache(cache_path: Path, metadata: dict[str, object], lexicon: CatalogLexicon) -> None:
    payload = {
        "metadata": metadata,
        "lexicon": {
            "categories": lexicon.categories,
            "brands": lexicon.brands,
            "feature_terms": sorted(lexicon.feature_terms),
            "attribute_values": lexicon.attribute_values,
        },
    }
    temporary = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        temporary.replace(cache_path)
    except OSError:
        # Read-only deployments still work: they simply keep the in-memory cache.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _slot_for_detail_key(key: object) -> str | None:
    normalized_key = normalize(key)
    for marker, slot in _DETAIL_SLOT_RULES:
        if marker in normalized_key:
            return slot
    return None


def _normalizable_detail_value(value: object) -> tuple[str, str] | None:
    if isinstance(value, (dict, list)):
        return None
    original = re.sub(r"\s+", " ", str(value)).strip(" .;,")
    normalized = normalize(original)
    words = re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)?", normalized)
    if (
        normalized in _IGNORED_DETAIL_VALUES
        or len(words) == 0
        or len(words) > 6
        or len(normalized) > 60
        or normalized.isdigit()
    ):
        return None
    return normalized, original


@lru_cache(maxsize=4)
def _load_catalog_lexicon(
    resolved_path: str,
    catalog_size: int,
    catalog_modified_ns: int,
    resolved_cache_path: str,
) -> CatalogLexicon:
    metadata = _cache_metadata(resolved_path, catalog_size, catalog_modified_ns)
    cache_path = Path(resolved_cache_path)
    cached = _read_disk_cache(cache_path, metadata)
    if cached is not None:
        return cached
    categories: dict[str, str] = {}
    brands: dict[str, str] = {}
    feature_counts: Counter[str] = Counter()
    attribute_values: dict[str, dict[str, str]] = {}
    with Path(resolved_path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            product = json.loads(line)
            for category in product.get("categories") or []:
                term = normalize(category)
                if term and term not in _GENERIC_CATEGORIES and len(term) >= 3:
                    categories.setdefault(term, str(category).strip())
            brand = str(product.get("store") or "").strip()
            normalized_brand = normalize(brand)
            if len(normalized_brand) >= 2:
                brands.setdefault(normalized_brand, brand)
            searchable = " ".join(
                [str(product.get("title") or ""), *map(str, product.get("features") or [])]
            ).lower()
            normalized_searchable = normalize(searchable)
            for term in FEATURE_CANDIDATES:
                if term in normalized_searchable:
                    feature_counts[term] += 1
            for key, value in (product.get("details") or {}).items():
                slot = _slot_for_detail_key(key)
                parsed_value = _normalizable_detail_value(value)
                if slot is None or parsed_value is None:
                    continue
                normalized_value, original_value = parsed_value
                attribute_values.setdefault(slot, {}).setdefault(normalized_value, original_value)
    lexicon = CatalogLexicon(
        categories=categories,
        brands=brands,
        feature_terms=frozenset(feature_counts),
        attribute_values=attribute_values,
    )
    _write_disk_cache(cache_path, metadata, lexicon)
    return lexicon
