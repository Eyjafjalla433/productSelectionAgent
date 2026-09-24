from __future__ import annotations

import re
from dataclasses import dataclass, field

from .catalog_lexicon import CatalogLexicon, normalize
from .models import ConstraintType, Intent


@dataclass
class ExtractedSlot:
    name: str
    value: str | float
    constraint_type: ConstraintType
    confidence: float = 0.9


@dataclass
class Extraction:
    intent: Intent
    confidence: float
    slots: list[ExtractedSlot] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(default_factory=dict)
    cleared_slots: list[str] = field(default_factory=list)
    conversion: bool = False
    comparison: bool = False


class RuleBasedExtractor:
    """Small deterministic baseline; replaceable by an LLM/local-model extractor."""

    CATEGORIES = {
        "dress": ["dress", "gown"],
        "shoes": ["shoe", "sneaker", "boot", "heel", "sandal", "loafer"],
        "handbag": ["bag", "handbag", "tote", "purse"],
        "jewelry": ["jewelry", "necklace", "ring", "earring", "bracelet"],
        "top": ["shirt", "top", "blouse", "tee", "t-shirt"],
        "outerwear": ["jacket", "coat", "blazer"],
    }
    COLORS = ("black", "blue", "red", "white", "green", "pink", "purple", "yellow", "brown", "navy", "beige", "grey", "gray")
    OCCASIONS = ("work", "office", "wedding", "party", "date", "casual", "everyday", "running", "gym")
    SOFT_TERMS = ("minimal", "slimming", "comfortable", "trendy", "casual", "formal", "elegant", "vintage")
    MATERIALS = ("cotton", "leather", "denim", "silk", "wool", "linen", "suede")
    GENDERS = {"women": "women", "woman": "women", "men": "men", "man": "men", "girls": "girls", "boys": "boys"}

    def __init__(self, catalog_lexicon: CatalogLexicon | None = None) -> None:
        self.catalog_lexicon = catalog_lexicon

    def extract(self, utterance: str) -> Extraction:
        text = utterance.lower()
        intent, confidence = self._intent(text)
        slots: list[ExtractedSlot] = []
        rejected: dict[str, list[str]] = {}
        cleared_slots = self._cleared_slots(text)

        for category, terms in self.CATEGORIES.items():
            if any(re.search(rf"\b{re.escape(term)}s?\b", text) for term in terms):
                slots.append(ExtractedSlot("category", category, ConstraintType.HARD))
                break
        if self.catalog_lexicon is not None:
            known_categories = self.catalog_lexicon.match_categories(text)
            if known_categories and not any(slot.name == "category" for slot in slots):
                slots.append(ExtractedSlot("category", known_categories[0], ConstraintType.HARD, 0.95))
            for brand in self.catalog_lexicon.match_brands(text)[:1]:
                if self._is_rejected(text, brand):
                    rejected.setdefault("brand", []).append(brand)
                else:
                    slots.append(ExtractedSlot("brand", brand, ConstraintType.HARD, 0.95))
            for feature in self.catalog_lexicon.match_features(text):
                name = f"feature_{feature.replace(' ', '_').replace('-', '_')}"
                if self._is_rejected(text, feature):
                    rejected.setdefault("feature", []).append(feature)
                else:
                    slots.append(ExtractedSlot(name, True, ConstraintType.HARD, 0.85))
            for name, value in self.catalog_lexicon.match_attributes(text):
                if self._is_rejected(text, value):
                    rejected.setdefault(name, []).append(value)
                    continue
                constraint_type = (
                    ConstraintType.SOFT
                    if name in {"fit", "pattern", "style", "occasion", "sport", "season", "care"}
                    else ConstraintType.HARD
                )
                slots.append(ExtractedSlot(name, value, constraint_type, 0.9))
        for color in self.COLORS:
            if re.search(rf"\b{color}\b", text):
                target = rejected if re.search(rf"\b(no|not|don't want|do not want)\s+{color}\b", text) else None
                if target is not None:
                    target.setdefault("color", []).append(color)
                else:
                    slots.append(ExtractedSlot("color", "gray" if color == "grey" else color, ConstraintType.HARD))
        for occasion in self.OCCASIONS:
            if re.search(rf"\b{occasion}\b", text):
                slots.append(ExtractedSlot("occasion", "work" if occasion == "office" else occasion, ConstraintType.HARD))
                break
        for term in self.SOFT_TERMS:
            if re.search(rf"\b{term}\b", text):
                slots.append(ExtractedSlot("style", term, ConstraintType.SOFT))
        for material in self.MATERIALS:
            if re.search(rf"\b{material}\b", text):
                slots.append(ExtractedSlot("material", material, ConstraintType.HARD))
                break
        for term, gender in self.GENDERS.items():
            if re.search(rf"\b{term}\b", text):
                slots.append(ExtractedSlot("gender", gender, ConstraintType.HARD))
                break
        size = re.search(r"\bsize\s*(xxs|xs|s|m|l|xl|xxl|\d{1,2})\b", text)
        if size:
            slots.append(ExtractedSlot("size", size.group(1).upper(), ConstraintType.HARD))

        price = re.search(r"(?:under|below|less than|up to|max(?:imum)? of?)\s*\$?\s*(\d+(?:\.\d+)?)", text)
        if price:
            slots.append(ExtractedSlot("price_max", float(price.group(1)), ConstraintType.HARD))
        price = re.search(r"(?:over|above|more than|at least)\s*\$?\s*(\d+(?:\.\d+)?)", text)
        if price:
            slots.append(ExtractedSlot("price_min", float(price.group(1)), ConstraintType.HARD))
        if re.search(r"\b(no|not|without)\s+(high )?heels?\b", text):
            rejected.setdefault("style", []).append("heels")
        rating = re.search(r"\b(?:rated\s+)?(\d(?:\.\d)?)\s*(?:\+|or more)?\s*stars?\b", text)
        if rating:
            slots.append(ExtractedSlot("rating_min", float(rating.group(1)), ConstraintType.HARD))
        review_count = re.search(r"\b(?:at least|over|more than)\s*(\d+)\s*(?:reviews?|ratings?)\b", text)
        if review_count:
            slots.append(ExtractedSlot("rating_number_min", float(review_count.group(1)), ConstraintType.HARD))
        self._extract_apparel_attributes(text, slots, rejected)

        return Extraction(
            intent=intent,
            confidence=confidence,
            slots=slots,
            rejected=rejected,
            cleared_slots=cleared_slots,
            conversion=bool(re.search(r"\b(i'll take|i will take|buy (it|this|that)|choose (it|this|that)|select (it|this|that))\b", text)),
            comparison=bool(re.search(r"\b(compare|which (one|is)|better between|vs\.?|versus)\b", text)),
        )

    @staticmethod
    def _is_rejected(text: str, value: str) -> bool:
        return bool(re.search(rf"\b(?:no|not|without|don't want|do not want)\s+{re.escape(normalize(value))}\b", text))

    @staticmethod
    def _extract_apparel_attributes(
        text: str,
        slots: list[ExtractedSlot],
        rejected: dict[str, list[str]],
    ) -> None:
        attributes = {
            "fit": ("slim", "relaxed", "regular", "loose", "oversized"),
            "sleeve": ("sleeveless", "short sleeve", "long sleeve", "three quarter sleeve"),
            "pattern": ("floral", "striped", "stripe", "plaid", "printed", "solid", "polka dot"),
        }
        for name, values in attributes.items():
            for value in values:
                if re.search(rf"\b{re.escape(value)}\b", text):
                    if RuleBasedExtractor._is_rejected(text, value):
                        rejected.setdefault(name, []).append(value)
                    else:
                        slots.append(ExtractedSlot(name, value, ConstraintType.SOFT))
                    break

    @staticmethod
    def _cleared_slots(text: str) -> list[str]:
        """Return constraints the customer explicitly withdraws this turn.

        Replacing a value (for example, ``blue instead``) is handled by the
        normal slot update path.  This method is for statements such as "any
        colour is fine" where there is no replacement value to extract.
        """

        patterns = {
            "color": r"\b(?:any|no|don't have a)\s+(?:particular\s+)?colou?r\b|\b(?:ignore|forget)\s+(?:my\s+)?(?:earlier\s+)?colou?r\b",
            "size": r"\b(?:any|no|don't have a)\s+(?:particular\s+)?size\b|\b(?:ignore|forget)\s+(?:my\s+)?(?:earlier\s+)?size\b",
            "occasion": r"\b(?:any|no|don't have a)\s+(?:particular\s+)?occasion\b|\b(?:ignore|forget)\s+(?:my\s+)?(?:earlier\s+)?occasion\b",
            "material": r"\b(?:any|no|don't have a)\s+(?:particular\s+)?material\b|\b(?:ignore|forget)\s+(?:my\s+)?(?:earlier\s+)?material\b",
            "style": r"\b(?:any|no|don't have a)\s+(?:particular\s+)?style\b|\b(?:ignore|forget)\s+(?:my\s+)?(?:earlier\s+)?style\b",
            "budget": r"\b(?:any|no|don't have a)\s+(?:particular\s+)?(?:budget|price)\b|\b(?:ignore|forget)\s+(?:my\s+)?(?:earlier\s+)?(?:budget|price)\b",
        }
        return [name for name, pattern in patterns.items() if re.search(pattern, text)]

    @staticmethod
    def _intent(text: str) -> tuple[Intent, float]:
        if re.search(r"\b(compare|which one|better)\b", text):
            return Intent.COMPARE, 0.9
        if re.search(r"\b(buy|purchase|need|looking for|show me|find me|under|below|budget)\b", text):
            return Intent.BUYING, 0.85
        if re.search(r"\b(ideas|inspiration|trending|popular|browse|explore)\b", text):
            return Intent.BROWSING, 0.8
        return Intent.UNKNOWN, 0.35
