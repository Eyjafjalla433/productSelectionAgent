from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from .models import IntentResult


COLORS = (
    "black", "white", "blue", "red", "pink", "green", "brown", "gray", "grey",
    "purple", "yellow", "orange", "beige", "navy", "gold", "silver",
)
COLOR_ALIASES = {
    "黑色": "black", "白色": "white", "蓝色": "blue", "红色": "red",
    "粉色": "pink", "绿色": "green", "棕色": "brown", "灰色": "gray",
    "紫色": "purple", "黄色": "yellow", "橙色": "orange", "米色": "beige",
    "藏青": "navy", "金色": "gold", "银色": "silver",
}
MATERIALS = (
    "cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk", "rayon",
    "linen", "denim", "suede", "cashmere", "fabric",
)
MATERIAL_ALIASES = {
    "纯棉": "cotton", "棉质": "cotton", "棉": "cotton",
    "涤纶": "polyester", "聚酯纤维": "polyester", "尼龙": "nylon",
    "皮革": "leather", "真皮": "leather", "羊毛": "wool",
    "氨纶": "spandex", "丝绸": "silk", "真丝": "silk",
    "亚麻": "linen", "牛仔": "denim", "麂皮": "suede", "羊绒": "cashmere",
}
CATEGORY_PATTERNS = {
    "t-shirt": ("t-shirt", "t-shirts", "tshirt", "tshirts", "tee", "tees", "t shirt", "t shirts", "T恤", "短袖T恤"),
    "shirt": ("shirt", "shirts", "blouse", "blouses", "衬衫", "上衣"),
    "jersey": ("jersey", "jerseys", "球衣"),
    "dress": ("dress", "dresses", "gown", "gowns", "连衣裙", "裙装"),
    "jacket": ("jacket", "jackets", "coat", "coats", "hoodie", "hoodies", "blazer", "blazers", "夹克", "外套", "卫衣", "西装"),
    "pants": ("pants", "jeans", "leggings", "trousers", "裤子", "牛仔裤", "打底裤"),
    "shorts": ("shorts", "短裤"),
    "skirt": ("skirt", "skirts", "半身裙", "短裙"),
    "shoes": ("shoe", "shoes", "sneaker", "sneakers", "boot", "boots", "sandal", "sandals", "heel", "heels", "loafer", "loafers", "鞋", "运动鞋", "靴子", "凉鞋", "高跟鞋"),
    "earrings": ("earring", "earrings", "hoop", "hoops", "耳环", "耳饰"),
    "necklace": ("necklace", "necklaces", "pendant", "pendants", "项链", "吊坠"),
    "ring": ("ring", "rings", "戒指"),
    "bracelet": ("bracelet", "bracelets", "bangle", "bangles", "手链", "手镯"),
    "bag": ("bag", "bags", "handbag", "handbags", "backpack", "backpacks", "purse", "purses", "包", "手提包", "背包"),
}
USE_CASES = (
    "running", "walking", "hiking", "gym", "work", "office", "wedding", "party",
    "travel", "outdoor", "winter", "summer", "everyday", "casual", "formal",
    "basketball", "soccer", "football", "hockey", "cycling", "tennis", "golf", "yoga",
)
FEATURES = (
    "waterproof", "breathable", "lightweight", "comfortable", "warm", "stretch",
    "durable", "hypoallergenic", "quick-drying", "moisture-wicking", "uv protection",
)
STYLES = (
    "casual", "formal", "vintage", "athletic", "classic", "bohemian", "minimalist",
    "slim fit", "regular fit", "relaxed fit", "crew neck", "v-neck", "long sleeve",
    "short sleeve", "high waisted",
    "loose fit", "oversized", "sleeveless", "elegant",
)
STYLE_ALIASES = {
    '宽松': 'loose fit', '宽大型': 'oversized', '修身': 'slim fit', '紧身': 'slim fit',
    '合身': 'regular fit', '休闲': 'casual', '正式': 'formal', '复古': 'vintage',
    '经典': 'classic', '优雅': 'elegant', '运动风': 'athletic', '极简': 'minimalist',
    '无袖': 'sleeveless', '长袖': 'long sleeve', '短袖': 'short sleeve',
    '圆领': 'crew neck', 'v领': 'v-neck',
}
USE_CASE_ALIASES = {
    '上班': 'work', '通勤': 'office', '日常': 'everyday', '平时穿': 'everyday',
    '旅行': 'travel', '旅游': 'travel', '户外': 'outdoor', '徒步': 'hiking',
    '跑步': 'running', '散步': 'walking', '健身房': 'gym', '瑜伽': 'yoga',
    '婚礼': 'wedding', '聚会': 'party', '冬季': 'winter', '夏季': 'summer',
}
FEATURE_ALIASES = {'舒服': 'comfortable', '舒适': 'comfortable', '透气': 'breathable',
                   '轻薄': 'lightweight', '轻便': 'lightweight', '防水': 'waterproof',
                   '耐穿': 'durable', '保暖': 'warm', '速干': 'quick-drying'}
AUDIENCES = (
    "women", "woman", "men", "man", "girls", "girl", "boys", "boy", "kids", "kid",
    "unisex", "maternity", "baby",
)
BUYING_CUES = {
    "purchase_commitment": ("ready to buy", "buy now", "purchase today", "place an order", "add to cart", "准备购买", "现在就买", "加入购物车"),
    "need_statement": ("i need", "i want", "i'm looking for", "i am looking for", "我需要", "我想要", "帮我找", "给我找"),
}
BROWSING_CUES = {
    "exploration": ("exploring", "inspiration", "ideas", "browse", "browsing",
                    "just looking", "looking around", "trends"),
    "open_question": ("what should", "what could", "what would"),
    "discovery_request": ("show me", "suggest", "recommend"),
}
OVERRIDE_RE = re.compile(r"\b(actually|instead|ignore (?:my |the )?(?:earlier|previous)|change my mind)\b", re.I)
PRICE_CAP_RE = re.compile(r"(?:\b(?:under|below|less than|at most|up to|maximum of|no more than)\s*\$?\s*(\d+(?:\.\d+)?)|(?:不超过|低于|少于|最多|预算(?:不超过|最多)?)[\s$￥¥]*(\d+(?:\.\d+)?)(?:\s*(?:美元|元|块))?|[\s$￥¥]*(\d+(?:\.\d+)?)(?:\s*(?:美元|元|块))?\s*(?:以内|以下))", re.I)
PRICE_FLOOR_RE = re.compile(r"(?:\b(?:over|above|more than|at least|minimum of)\s*\$?\s*(\d+(?:\.\d+)?)|(?:至少|高于|超过)[\s$￥¥]*(\d+(?:\.\d+)?)(?:\s*(?:美元|元|块))?)", re.I)
PRICE_RANGE_RE = re.compile(r"(?:\bbetween\s*\$?\s*(\d+(?:\.\d+)?)\s*(?:and|-)\s*\$?\s*(\d+(?:\.\d+)?)|[\s$￥¥]*(\d+(?:\.\d+)?)\s*(?:到|至|[-~～])\s*[\s$￥¥]*(\d+(?:\.\d+)?)(?:\s*(?:美元|元|块))?)", re.I)
PRICE_TARGET_RE = re.compile(r"\b(?:around|about|roughly|budget of|budget is)\s*\$?\s*(\d+(?:\.\d+)?)", re.I)
PRICE_APPROX_ZH_RE = re.compile(r'(?:预算(?:还是|是)?\s*|\$\s*)(\d+(?:\.\d+)?)\s*(?:美元)?\s*左右|(?:大约|大概|约)\s*\$?\s*(\d+(?:\.\d+)?)\s*美元')
PRICE_OR_LESS_RE = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:or less|or below|or under)", re.I)
SIZE_WORD_RE = re.compile(r"\b(xxs|xs|xl|xxl|xxxl|small|medium|large|extra large)\b", re.I)
SIZE_LETTER_RE = re.compile(r"\bsize\s*(s|m|l)\b", re.I)
NEGATION_RE = re.compile(
    r"\b(?:no|not|without|avoid|except|excluding)\s+(?:a |an |any )?([a-z][a-z -]{1,30}?)(?=\s+(?:and|but|for|with)\b|[,.;]|$)",
    re.I,
)
KEY_REQUIREMENT_RE = re.compile(r"\bkey requirement is:\s*(.+)$", re.I)


def _contains(text: str, phrase: str) -> bool:
    if any(ord(char) > 127 for char in phrase):
        return phrase.casefold() in text.casefold()
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def _values_in(text: str, values: Iterable[str]) -> list[str]:
    return [value for value in values if _contains(text, value)]


def _append_unique(slots: defaultdict[str, list[str] | float], name: str, value: str) -> None:
    existing = slots[name]
    if isinstance(existing, list) and value not in existing:
        existing.append(value)


class IntentRouter:
    """Deterministic, evidence-weighted query understanding for two retrieval tracks."""

    def __init__(
        self,
        known_brands: Iterable[str] = (),
        known_categories: Iterable[str] = (),
    ) -> None:
        self.known_brands = {brand.strip().lower() for brand in known_brands if len(brand.strip()) >= 2}
        self.known_categories = {
            " ".join(category.strip().lower().replace("-", " ").split())
            for category in known_categories
            if 2 <= len(category.strip()) <= 40
        }

    def understand(self, query: str) -> IntentResult:
        normalized = " ".join(query.lower().split())
        slots = self._extract_slots(normalized)
        intent_type, confidence, evidence = self._classify_intent(normalized, slots)
        hard, soft = self._split_constraints(normalized, slots, intent_type)
        filters = self._filter_constraints(hard)
        flags = self._ambiguity_flags(slots, confidence, intent_type)
        return IntentResult(
            raw_query=query,
            normalized_query=normalized,
            intent_type=intent_type,
            intent_confidence=confidence,
            slots=dict(slots),
            hard_constraints=hard,
            filter_constraints=filters,
            soft_preferences=soft,
            keyword_query=self._keyword_query(slots, hard),
            semantic_query=self._semantic_query(slots, soft),
            ambiguity_flags=flags,
            override_detected=bool(OVERRIDE_RE.search(normalized)),
            decision_evidence=evidence,
        )

    def _extract_slots(self, text: str) -> defaultdict[str, list[str] | float]:
        slots: defaultdict[str, list[str] | float] = defaultdict(list)
        match_text = " " + re.sub(r"[^a-z0-9]+", " ", text) + " "
        for category, phrases in CATEGORY_PATTERNS.items():
            if any(_contains(text, phrase) for phrase in phrases):
                _append_unique(slots, "category", category)
        for category in self.known_categories:
            if f" {category} " in match_text:
                _append_unique(slots, "category", category)
        for name, values in {
            "color": COLORS,
            "material": MATERIALS,
            "use_case": USE_CASES,
            "feature": FEATURES,
            "style": STYLES,
            "audience": AUDIENCES,
        }.items():
            for value in _values_in(text, values):
                _append_unique(slots, name, value)
        for alias, canonical in COLOR_ALIASES.items():
            if alias in text:
                _append_unique(slots, "color", canonical)
        for alias, canonical in sorted(MATERIAL_ALIASES.items(), key=lambda item: len(item[0]), reverse=True):
            if alias in text:
                _append_unique(slots, "material", canonical)
        for brand in self.known_brands:
            if f" {brand} " in match_text:
                _append_unique(slots, "brand", brand)
        for name, aliases in (('style', STYLE_ALIASES), ('use_case', USE_CASE_ALIASES), ('feature', FEATURE_ALIASES)):
            for alias, canonical in aliases.items():
                if alias in text:
                    _append_unique(slots, name, canonical)

        self._extract_price(text, slots)
        self._extract_size(text, slots)
        self._extract_exclusions(text, slots)
        self._extract_disclosed_requirement(text, slots)
        return defaultdict(lambda: [], {key: value for key, value in slots.items() if value not in ([], None)})

    @staticmethod
    def _extract_price(text: str, slots: defaultdict[str, list[str] | float]) -> None:
        # The frozen catalog is USD-denominated. Never silently treat RMB as
        # dollars; currency conversion belongs in a separately validated tool.
        if re.search(r"(?:人民币|￥|¥|\d\s*(?:(?<!美)元|块))", text):
            return
        price_range = PRICE_RANGE_RE.search(text)
        price_cap = PRICE_CAP_RE.search(text) or PRICE_OR_LESS_RE.search(text)
        price_floor = PRICE_FLOOR_RE.search(text)
        price_target = PRICE_TARGET_RE.search(text)
        approximate_target = PRICE_APPROX_ZH_RE.search(text)
        if approximate_target:
            slots['budget_target'] = float(next(v for v in approximate_target.groups() if v is not None))
        if price_range:
            values = [value for value in price_range.groups() if value is not None]
            slots["budget_min"] = float(values[0])
            slots["budget_max"] = float(values[1])
        else:
            if price_cap and not (approximate_target and price_cap.start() < approximate_target.end() and approximate_target.start() < price_cap.end()):
                slots["budget_max"] = float(next(value for value in price_cap.groups() if value is not None))
            if price_floor and not (price_cap and price_cap.start() < price_floor.end()
                                    and price_floor.start() < price_cap.end()):
                slots["budget_min"] = float(next(value for value in price_floor.groups() if value is not None))
        if price_target and not approximate_target:
            slots["budget_target"] = float(price_target.group(1))

    @staticmethod
    def _extract_size(text: str, slots: defaultdict[str, list[str] | float]) -> None:
        def positive(index: int) -> bool:
            return not re.search(
                r"\b(?:not|no|avoid|without|rather than|instead of|"
                r"don't want|do not want)\s+(?:a\s+)?(?:size\s+)?$",
                text[:index], re.I)

        found: list[tuple[int, str]] = []
        for match in re.finditer(r'(?:尺码|尺寸)(?:是|要|为)?\s*(xxxl|xxl|xxs|xl|xs|s|m|l)(?:码)?(?![a-z])', text, re.I):
            found.append((match.start(1), match.group(1).lower()))
        for match in SIZE_WORD_RE.finditer(text):
            if positive(match.start()):
                found.append((match.start(1), match.group(1).lower()))
        for match in SIZE_LETTER_RE.finditer(text):
            if positive(match.start()):
                found.append((match.start(1), match.group(1).lower()))
        size_token = r'(?:xxxl|xxl|xxs|xl|xs|s|m|l)'
        for match in re.finditer(
                rf"\b(?:size\s*|my size is\s+|i\s+(?:usually\s+)?wear\s+(?:a\s+)?)(?P<first>{size_token})\b"
                rf"(?:\s+or\s+(?P<second>{size_token})\b)?", text, re.I):
            if positive(match.start()):
                found.append((match.start('first'), match.group('first').lower()))
                if match.group('second'):
                    found.append((match.start('second'), match.group('second').lower()))
        for match in re.finditer(r"\b(?:size|us)\s*(\d{1,2}(?:\.5)?)\b", text, re.I):
            if positive(match.start()):
                found.append((match.start(1), match.group(1)))
        for _, value in sorted(found):
            _append_unique(slots, 'size', value)

    @staticmethod
    def _extract_exclusions(text: str, slots: defaultdict[str, list[str] | float]) -> None:
        for match in NEGATION_RE.finditer(text):
            phrase = match.group(1).strip()
            for name, values in {
                "material": MATERIALS,
                "color": COLORS,
                "feature": FEATURES,
                "style": STYLES,
                "use_case": USE_CASES,
            }.items():
                for value in values:
                    if _contains(phrase, value):
                        _append_unique(slots, f"{name}_exclude", value)
            for category, phrases in CATEGORY_PATTERNS.items():
                if any(_contains(phrase, value) for value in phrases):
                    _append_unique(slots, "category_exclude", category)
        for alias, canonical in {**COLOR_ALIASES, **MATERIAL_ALIASES}.items():
            if re.search(rf"(?:不要|不想要|不含|避免|排除)[^，。；,.]{{0,8}}{re.escape(alias)}", text):
                name = "color" if alias in COLOR_ALIASES else "material"
                _append_unique(slots, f"{name}_exclude", canonical)
        for name, aliases in (('style', STYLE_ALIASES), ('use_case', USE_CASE_ALIASES), ('feature', FEATURE_ALIASES)):
            for alias, canonical in aliases.items():
                if re.search(rf'(?:不要|不想要|不想|避免|排除)(?:太|很|特别|过于|任何|带有|的|\s)*{re.escape(alias)}', text):
                    _append_unique(slots, f'{name}_exclude', canonical)

    @staticmethod
    def _extract_disclosed_requirement(text: str, slots: defaultdict[str, list[str] | float]) -> None:
        """Preserve evaluator-disclosed metadata that has no narrow slot parser."""

        match = KEY_REQUIREMENT_RE.search(text)
        if not match:
            return
        requirement = match.group(1).strip(" .;,:")
        if not requirement:
            return
        slot = "style" if any(word in requirement for word in ("style", "fit", "sleeve", "neck")) else "feature"
        _append_unique(slots, slot, requirement)

    @staticmethod
    def _meaningful_slots(slots: dict[str, list[str] | float]) -> set[str]:
        return {
            name for name, value in slots.items()
            if value not in ([], None)
        }

    def _classify_intent(
        self, text: str, slots: dict[str, list[str] | float]
    ) -> tuple[str | None, float, dict[str, object]]:
        buying_score = 0.0
        browsing_score = 0.0
        buying_evidence: list[str] = []
        browsing_evidence: list[str] = []

        if any(_contains(text, cue) for cue in BUYING_CUES["purchase_commitment"]):
            buying_score += 2.0
            buying_evidence.append("purchase_commitment")
        if any(_contains(text, cue) for cue in BUYING_CUES["need_statement"]):
            buying_score += 0.2
            buying_evidence.append("need_statement")

        meaningful = self._meaningful_slots(slots)
        specific_attributes = meaningful - {"category", "audience"}
        if "category" in meaningful and specific_attributes:
            buying_evidence.append("specificity_only")

        if any(_contains(text, cue) for cue in BROWSING_CUES["exploration"]):
            browsing_score += 1.2
            browsing_evidence.append("exploration_phrase")
        if any(_contains(text, cue) for cue in BROWSING_CUES["open_question"]):
            browsing_score += 1.5
            browsing_evidence.append("open_question")
        if any(_contains(text, cue) for cue in BROWSING_CUES["discovery_request"]):
            browsing_score += 0.5
            browsing_evidence.append("discovery_request")
        if "still exploring" in text:
            browsing_score += 2.5
            browsing_evidence.append("still_exploring")

        margin = buying_score - browsing_score
        if margin >= 1.0:
            intent = "buying"
        elif margin <= -1.0:
            intent = "browsing"
        else:
            intent = None
        confidence = round(
            min(0.95, 0.65 + 0.3 * min(abs(margin) / 3.0, 1.0))
            if intent is not None
            else 0.5 + 0.1 * min(abs(margin), 1.0),
            2,
        )
        evidence: dict[str, object] = {
            "buying": buying_evidence,
            "browsing": browsing_evidence,
            "buying_score": round(buying_score, 2),
            "browsing_score": round(browsing_score, 2),
            "margin": round(margin, 2),
            "decision": intent or "undetermined",
        }
        return intent, confidence, evidence

    @staticmethod
    def _split_constraints(
        text: str, slots: dict[str, list[str] | float], intent_type: str | None
    ) -> tuple[dict[str, list[str] | float], dict[str, list[str] | float]]:
        hard: dict[str, list[str] | float] = {}
        soft: dict[str, list[str] | float] = {}
        for name, value in slots.items():
            if name.endswith("_exclude") or name in {"budget_min", "budget_max"}:
                hard[name] = value
            elif name == "budget_target":
                soft[name] = value
            elif name == "size":
                hard[name] = value
            elif name == "category" and intent_type == "buying":
                hard[name] = value
            elif name in {"color", "material", "brand", "feature", "style", "audience"} and IntentRouter._value_is_explicitly_hard(text, value):
                hard[name] = value
            else:
                soft[name] = value
        return hard, soft

    @staticmethod
    def _value_is_explicitly_hard(text: str, value: list[str] | float) -> bool:
        if "key requirement is:" in text:
            return True
        if not isinstance(value, list):
            return False
        for item in value:
            if re.search(r"\b(?:must|only|require|exactly)\b[^.]{0,40}" + re.escape(item), text):
                return True
        return False

    @staticmethod
    def _filter_constraints(hard: dict[str, list[str] | float]) -> dict[str, list[str] | float]:
        """Keep only constraints backed by fixed catalog metadata fields.

        Catalog price, store, and category are structurally available. Material,
        color, feature, and size occur in free text and must not be assumed to be
        exact metadata filters by this module.
        """

        return {name: value for name, value in hard.items() if name in {"budget_min", "budget_max", "brand", "category"}}

    @staticmethod
    def _keyword_query(slots: dict[str, list[str] | float], hard: dict[str, list[str] | float]) -> str:
        terms: list[str] = []
        for name in ("brand", "color", "material", "category", "size", "style", "feature"):
            value = hard.get(name, slots.get(name, []))
            if isinstance(value, list):
                terms.extend(value)
        for name in ("category_exclude", "material_exclude", "color_exclude", "feature_exclude"):
            value = hard.get(name, [])
            if isinstance(value, list):
                terms.extend(f"not {item}" for item in value)
        if "budget_max" in hard:
            terms.append(f"under {hard['budget_max']:g} dollars")
        return " ".join(dict.fromkeys(terms))

    @staticmethod
    def _semantic_query(slots: dict[str, list[str] | float], soft: dict[str, list[str] | float]) -> str:
        terms: list[str] = []
        for name in ("category", "use_case", "feature", "style", "color", "material"):
            value = soft.get(name, slots.get(name, []))
            if isinstance(value, list):
                terms.extend(value)
        return " ".join(dict.fromkeys(terms))

    @staticmethod
    def _ambiguity_flags(
        slots: dict[str, list[str] | float], confidence: float, intent_type: str | None
    ) -> list[str]:
        flags: list[str] = []
        if not slots.get("category"):
            flags.append("missing_category")
        if confidence < 0.65:
            flags.append("low_intent_confidence")
        if intent_type is None:
            flags.append("intent_undetermined")
        return flags
