"""Incremental turn parsing for the integrated agent; legacy Router is unchanged."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import re

from .models import SlotUpdate
from .router import IntentRouter, CATEGORY_PATTERNS, _contains


RESULT_CONTROL_RE = re.compile(
    r"^(?:please\s+)?(?:show|give|list|surface|display|recommend)\s+"
    r"(?:me\s+)?(?:the\s+)?(?:strongest|best|top|matching|current|remaining|some\s+more|more)?\s*"
    r"(?:matches|results|options|recommendations|products|items|ones|more)"
    r"(?:\s+(?:now|again))?[.!?]*$",
    re.I,
)
CHINESE_RESULT_CONTROL_RE = re.compile(r"^(?:请)?(?:给我|帮我)?(?:展示|显示|推荐|看看)?(?:最匹配|最好|更多|剩余|当前)?(?:的)?(?:商品|产品|结果|选项|推荐|一些)?(?:再来一些|更多)?[。！？!?]*$")


def feature_slot(text: str) -> str:
    return "feature_" + hashlib.sha256(text.lower().encode()).hexdigest()[:12]


class TurnIntentRouter(IntentRouter):
    def understand_turn(self, message: str, *, pending_question: dict | None = None):
        parsed = self.understand(message)
        text = parsed.normalized_query
        pending = pending_question or {}
        updates = []
        if re.fullmatch(r'(?:please\s+)?(?:retry|try again|search again|重试|再试一次|再搜一次|重新搜索)[.!?。！？]*', text):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence, 'requested_results': True,
                                              'retry_search': True})
        # Resolve an exact displayed option using the current question only.
        # Keep free-form/multi-detail replies on the normal extraction path.
        from .option_labels import OPTION_LABELS_ZH
        for value, label in pending.get('option_labels', {}).items():
            if text.rstrip(' .!?。！？') in {label.casefold(), OPTION_LABELS_ZH.get(value, value)} and value in pending.get('options', ()):
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=(SlotUpdate(pending['target_slot'], 'set',
                               (value,), pending.get('constraint_type', 'soft'), 1.0, message),))
        # Uncertainty is a conversation signal, never a literal slot value.
        # Keep independently stated requirements, but don't press for another
        # preference in the same turn or silently withdraw existing constraints.
        parts = re.split(r'\s*[,，;；。!?！？]\s*|\s+(?:but|and)\s+|但是|但', text)
        uncertain = re.compile(r"(?:(?:i(?:'m| am)\s+)?not sure(?: yet)?|i (?:don't|do not) know|no idea|(?:我)?(?:也|还)?(?:不确定|不知道|没想好)|(?:我)?拿不准)[.。]*")
        if any(uncertain.fullmatch(part.strip()) for part in parts):
            remaining = ', '.join(part for part in parts if part.strip() and not uncertain.fullmatch(part.strip()))
            extra = self.understand_turn(remaining, pending_question=pending) if remaining else None
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=extra.slot_updates if extra else (),
                           decision_evidence={**parsed.decision_evidence, **(extra.decision_evidence if extra else {}),
                                              'requested_results': True, 'uncertain_preference': True})
        show_prefix = re.match(r'(?:先看看|先看结果|直接推荐|别问了|不用问了|跳过|show me first|just show me|skip)(?:\s*[,，;；]\s*(?:(?:but|and)\s+|但|另外)?|\s+(?:but|and)\s+)(.+)$', text)
        if show_prefix:
            extra = self.understand_turn(show_prefix.group(1))
            return replace(parsed, slot_updates=extra.slot_updates,
                           decision_evidence={**extra.decision_evidence, 'requested_results': True})
        if RESULT_CONTROL_RE.fullmatch(text) or re.fullmatch(r'(?:先看看|先看结果|直接推荐|别问了|不用问了|跳过|看看更多|再看一些|换一批|skip|just show me|show me first)[.!?。！？]*', text):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence, 'requested_results': True,
                                              'requested_more': bool(re.search(r'\bmore\b|看看更多|再看一些|换一批', text))})
        if re.fullmatch(r'(?:hello|hi|hey|你好|您好|thanks|thank you|okay|ok|谢谢|好的|好)[.!?。！？]*', text):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence, 'conversation_act': None if re.match(r'hello|hi|hey|你好|您好', text) else 'acknowledgement'})
        if re.fullmatch(r'(?:wait|hold on|one moment|let me think|等一下|稍等|让我想想|我想想)[.!?。！？]*', text):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence, 'conversation_act': 'pause'})
        if re.fullmatch(r"(?:please\s+)?(?:undo(?:\s+(?:that|the last change|my last change|my last requirement))?|撤销(?:刚才的修改|刚才的条件|上一步|上次修改)?|撤回刚才的修改)[.!?。！？]*", text):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence, 'requirement_control': 'undo'})

        if re.fullmatch(r'(?:please\s+)?(?:redo(?:\s+(?:that|the last change))?|重做(?:刚才的修改|上一步)?|恢复刚才的修改|还是恢复刚才的修改)[.!?。！？]*', text):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence, 'requirement_control': 'redo'})

        def add(slot, op, values=(), tier=None):
            updates.append(SlotUpdate(slot, op, tuple(values), tier, 0.9, message))

        correction = pending.get('correction')
        if correction:
            choice = None
            parts = re.split(r'\s*[,，;；]\s*(?:(?:but|and)\s+|但|另外)?|\s+(?:but|and)\s+', text, maxsplit=1)
            answer = parts[0]
            if re.fullmatch(r'(?:both|either|keep both|两种都看|两个都要|都看看|都可以)[.!?。！？]*', answer):
                choice = tuple(dict.fromkeys((*correction['old'], *correction['new'])))
            elif re.fullmatch(r'(?:replace|replace it|use the new one|换成新的|换新的|替换|就新的)[.!?。！？]*', answer):
                choice = tuple(correction['new'])
            elif re.fullmatch(r'(?:keep original|keep the original|keep the old one|no|nope|保留原来的|还是原来的|不改了)[.!?。！？]*', answer):
                choice = tuple(correction['old'])
            if choice is not None:
                add(correction['slot'], 'remove_exclusion', choice)
                add(correction['slot'], 'set', choice, 'hard')
                extra = self.understand_turn(parts[1]) if len(parts) > 1 else None
                if extra:
                    updates.extend(extra.slot_updates or ())
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=tuple(updates),
                               decision_evidence={**parsed.decision_evidence, **(extra.decision_evidence if extra else {})})

        # No-preference replies are explicit withdrawals, not positive values.
        cleared = set()
        names = {"color": "colou?r", "material": "material", "brand": "brand", "size": "size", "style": "style", "use_case": "(?:use.case|occasion)", "budget": "(?:budget|price)"}
        for slot, pattern in names.items():
            if re.search(rf"(?:any\s+{pattern}|no\s+(?:additional\s+)?preference\s+for\s+{pattern}|{pattern}\s+(?:is\s+)?(?:unlimited|unrestricted)|{pattern}\s+(?:doesn't|does not)\s+matter|(?:ignore|forget)\s+(?:my\s+)?(?:earlier\s+)?{pattern}|no\s+{pattern}\s+(?:limit|restriction))", text):
                cleared.add(slot)
        chinese_clear = {
            "color": ("颜色不限", "颜色无所谓"),
            "material": ("材质不限", "材质无所谓"),
            "brand": ("品牌不限", "品牌无所谓"),
            "size": ("尺码不限", "尺寸不限"),
            "style": ("风格不限", "款式不限"),
            "use_case": ("场合不限", "用途不限"),
            "budget": ("预算不限", "价格不限", "没有预算限制"),
        }
        for slot, phrases in chinese_clear.items():
            if any(phrase in text for phrase in phrases):
                cleared.add(slot)
        # Only an unqualified reply refers to the pending question. For example,
        # "material doesn't matter, black please" must not clear a color answer.
        clauses = re.split(r'[,，;；.!?。！？]|\b(?:but|and)\b|但是|但', text)
        generic_indifference = any(re.fullmatch(
            r"(?:(?:i\s+)?(?:have\s+no|no|don't have (?:a|an))\s+(?:additional\s+)?preference|(?:it\s+)?doesn't matter|any is fine|都行|随便|无所谓)",
            clause.strip()) for clause in clauses)
        if pending and generic_indifference:
            cleared.add(pending["target_slot"])
        for slot in sorted(cleared):
            for name in (("price_min", "price_max", "budget_target") if slot == "budget" else (slot,)):
                add(name, "clear")

        # Official disclosed descriptions remain opaque features. In particular,
        # 'gift for ... kids' and 'Goddess' do not become category/brand changes.
        disclosure = re.match(r"for that, what matters is:\s*(.*)", text)
        if disclosure:
            values = [v.strip(" .") for v in disclosure.group(1).split(";") if v.strip(" .")]
            target = pending.get("target_slot", "other")
            tier = pending.get("constraint_type", "soft")
            hard_limit = int(pending.get("hard_value_limit", len(values) if tier == "hard" else 0))
            for index, value in enumerate(values):
                value_tier = "hard" if index < hard_limit else "soft"
                if target in {"color", "material", "size", "brand", "style", "category"} and len(value.split()) <= 4:
                    add(target, "set", (value,), value_tier if target != "category" else "hard")
                elif target == "budget" and re.search(r"\d", value):
                    numeric = self.understand(value)
                    for key in ("budget_min", "budget_max", "budget_target"):
                        if key in numeric.slots:
                            add(key, "set", (numeric.slots[key],), "soft" if key == "budget_target" else "hard")
                else:
                    # `other` disclosures are independent evidence phrases.
                    # Do not collapse two values such as "polyester" and
                    # "60% polyester" into one material slot.
                    add(feature_slot(value), "set", (value,), value_tier)
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={}, filter_constraints={}, slot_updates=tuple(updates))

        initial = re.match(r"i(?:'m| am) looking for (.+?)(?:\.\s|, but|\.$|$)", text)
        category = initial.group(1).strip(" .") if initial else None
        if category and (RESULT_CONTROL_RE.fullmatch(text) or CHINESE_RESULT_CONTROL_RE.fullmatch(text)):
            category = None
        if category and (re.search(r"\b(?:under|over|below|above|prefer|not|without)\b|\$", category) or any(v in category.split() for v in parsed.slots.get("color", []))):
            category = None
        # A recognized product name is canonical even in a descriptive opening.
        category_text = initial.group(1) if initial else text
        recognized = [name for name, phrases in CATEGORY_PATTERNS.items()
                      if any(_contains(category_text, phrase) for phrase in phrases)]
        if recognized:
            category = recognized[0]
        direct = bool(re.search(r"\b(?:need|want|show me|find|looking for|switch to|change to)\b|(?:我需要|我想要|帮我找|给我找|换成|改成|找一?[个件双条款]?)", text))
        if not category:
            for name, phrases in CATEGORY_PATTERNS.items():
                if any(_contains(text, phrase) for phrase in phrases):
                    if direct or pending.get("target_slot") == "category" or len(text.split()) <= 4 or len(recognized) == 1:
                        category = name
                        break
        if category:
            add("category", "set", (category,), "hard")

        key = re.search(r"key requirement is:\s*(.+)", text)
        override = re.search(r"what i need is:\s*(.+)", text)
        if key or override:
            value = (key or override).group(1).strip(" .")
            if override:
                # Generic anaphora withdraws only the most recently disclosed
                # soft preference. Structured slots below replace their own value.
                add("latest_preference", "clear")
            parsed_value = self.understand(value)
            structured = next(((name, raw) for name, raw in parsed_value.slots.items()
                               if name in {"color", "material", "size", "style", "use_case"}
                               and len(value.split()) <= 4), None)
            if structured:
                name, raw = structured
                add(name, "set", tuple(raw if isinstance(raw, list) else (raw,)), "hard")
            else:
                add(feature_slot(value), "set", (value,), "hard")
        # A trailing initial description in override sessions is a preference,
        # not a signal that the user wants every category/store mentioned in it.
        if initial and not key and not override:
            trailing = text[initial.end():].strip(" .")
            if trailing and "still exploring" not in trailing:
                add(feature_slot(trailing), "set", (trailing,), "soft")

        scoped_text = text.split(". a key requirement is:")[0] if key else text
        # Ordinary multi-sentence input contributes all its structured slots,
        # even while answering a question about just one attribute.
        # Preserve the evaluation protocol's opaque, field-labelled disclosure.
        if initial and re.match(r'(?:material|feature|description):', text[initial.end():].strip(' .')):
            scoped_text = text[:initial.end()]
        if override:
            scoped_text = ""  # The disclosed requirement above is the update.
        scoped = self.understand(scoped_text)
        for name, raw in scoped.slots.items():
            if name in {"category", "audience"} or name.endswith("_exclude"):
                continue
            if name not in {"color", "material", "size", "brand", "style", "use_case", "feature", "budget_min", "budget_max", "budget_target"}:
                continue
            if name in cleared or (name.startswith("budget_") and "budget" in cleared):
                continue
            values = raw if isinstance(raw, list) else [raw]
            rejected = scoped.slots.get(name + "_exclude", [])
            values = [v for v in values if v not in rejected]
            if not values:
                continue
            # Catalog stores include ordinary words such as 'switch' and 'not'.
            # Require an explicit brand cue instead of making those hard filters.
            if name == "brand" and not (pending.get("target_slot") == "brand" or re.search(r"\b(?:brand|by|from)\b", scoped_text)):
                continue
            if name == "color" and re.search(r"\b(?:also|too)\b.*\b(?:fine|okay|ok)\b|\bis\s+(?:also\s+)?(?:fine|okay|ok)\b", text):
                add(name, "remove_exclusion", values)
                add(name, 'set', values, 'soft')
                continue
            # Preference wording applies to its clause, not every extracted slot.
            clauses = re.split(r"[,;.，。；]|\b(?:but|however|whereas)\b|但是|然而|不过|但|\band\s+(?=(?:i\s+)?(?:must|need|prefer|preferably|ideally)\b)", scoped_text)
            def mentions_value(clause):
                extracted = self.understand(clause).slots.get(name, [])
                extracted = extracted if isinstance(extracted, list) else [extracted]
                return any(v in extracted or _contains(clause, str(v)) for v in values)
            value_clause = next((clause for clause in clauses if mentions_value(clause)), scoped_text)
            soft = name in {"style", "use_case", "feature", "budget_target"} or bool(re.search(r"\b(?:prefer|preferably|maybe|ideally|like)\b|(?:最好|偏好|希望|尽量|也不错|也可以|或许|可能)", value_clause))
            if name in {"budget_min", "budget_max"}:
                soft = False
            # Explicit revision can demote/replace a previous hard requirement.
            # Ordinary tentative preferences still cannot silently erase it.
            optional = bool(re.search(r'\b(?:optional|not required|not essential)\b|(?:不是必须|不强求|非必需)', value_clause))
            mandatory = bool(re.search(r'\b(?:must|required|essential|non[ -]negotiable)\b|(?:必须|一定要|只接受)', value_clause))
            if mandatory and not optional and name != 'budget_target':
                soft = False
            revision = bool(re.search(r'\b(?:actually|instead|changed my mind|change my mind)\b|(?:改成|改为|换成|改主意)', scoped_text))
            if name in {'color', 'material', 'size', 'brand', 'style', 'use_case'} and (optional or (soft and revision)):
                add(name, 'clear')
                soft = True
            add(name, "remove_exclusion", values)
            add(name, "set", values, "soft" if soft else "hard")
        for name, values in scoped.slots.items():
            if name.endswith("_exclude"):
                add(name[:-8], "exclude", values)
        # Short answers to a structured question need not repeat the slot name.
        if not updates and pending and not correction and len(text.split()) <= 5 and text.strip(" .") and not RESULT_CONTROL_RE.fullmatch(text):
            target = pending["target_slot"]
            if target in {"category", "color", "material", "brand", "size", "style", "use_case"}:
                add(target, "set", (text.strip(" ."),), "hard" if target == "category" else pending.get("constraint_type", "soft"))
            elif target in {"feature", "other"}:
                add(feature_slot(text.strip(" .")), "set", (text.strip(" ."),), pending.get("constraint_type", "soft"))
            elif target == "budget" and re.search(r"\d", text):
                number = float(re.search(r"\d+(?:\.\d+)?", text).group())
                add("budget_max", "set", (number,), "hard")
        evidence = {**parsed.decision_evidence, "negative_feedback": "not quite right" in text or "none of these" in text or "不太合适" in text or "这些都不行" in text}
        # Preserve the stronger meaning instead of collapsing pure cotton into
        # any cotton content. The same value works for preferences/exclusions.
        if re.search(r'纯棉|\b(?:100\s*%\s*|pure\s+|all[ -])cotton\b', text):
            updates = [replace(update, values=tuple('100% cotton' if value == 'cotton' else value for value in update.values))
                       if update.slot == 'material' else update for update in updates]
        return replace(parsed, slot_updates=tuple(updates), decision_evidence=evidence)
