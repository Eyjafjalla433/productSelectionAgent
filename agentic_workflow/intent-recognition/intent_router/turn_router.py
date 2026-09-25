"""Incremental turn parsing for the integrated agent; legacy Router is unchanged."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import re

from .models import SlotUpdate
from .router import IntentRouter, CATEGORY_PATTERNS, COLORS, _contains


RESULT_CONTROL_RE = re.compile(
    r"^(?:please\s+)?(?:show|give|list|surface|display|recommend)\s+"
    r"(?:me\s+)?(?:the\s+)?(?:strongest|best|top|matching|current|remaining|some\s+more|more)?\s*"
    r"(?:matches|results|options|recommendations|products|items|ones|more)"
    r"(?:\s+(?:now|again))?[.!?]*$",
    re.I,
)
OPTION_ACCEPTANCE = (
    r'(?:(?:either(?: one)?|any(?: of (?:those|them))?|'
    r'all(?: of (?:those|them))?)\s+(?:is|are)\s+'
    r'(?:fine|okay|ok|good)(?: with me)?|'
    r'either(?: color| colour| style| fabric| material| fit| one| option)?'
    r'\s+works(?: for me)?|'
    r"i(?:'m| am) open to either(?: one| option)?|"
    r"i (?:don't|do not) mind either|"
    r"no strong preference|i(?:'m| am) flexible)"
)
CHINESE_RESULT_CONTROL_RE = re.compile(r"^(?:请)?(?:给我|帮我)?(?:展示|显示|推荐|看看)?(?:最匹配|最好|更多|剩余|当前)?(?:的)?(?:商品|产品|结果|选项|推荐|一些)?(?:再来一些|更多)?[。！？!?]*$")


NEGATIVE_BATCH_RE = re.compile(
    r"\b(?:none of (?:these|those)(?: (?:items|options|products))?\s+(?:would\s+)?work(?: for me)?|"
    r"(?:these|those|they|the options?|the items?)\s+(?:don't|do not)\s+work(?: for me)?|"
    r"none of (?:these|those)(?: (?:items|options|products))?\s+(?:are|feel)\s+right|"
    r"i\s+(?:don't|do not)\s+like any of (?:these|those))\b", re.I)


def feature_slot(text: str) -> str:
    return "feature_" + hashlib.sha256(text.lower().encode()).hexdigest()[:12]


GIFT_RECIPIENTS = (r'dad|father|mom|mother|wife|husband|partner|friend|'
                   r'son|daughter|boyfriend|girlfriend|kids?|baby|sister|brother|'
                   r'grandma|grandpa|grandmother|grandfather|aunt|uncle|colleague|coworker')
SHOPPING_OCCASIONS = {
    'birthday': r'\bbirthdays?\b',
    'graduation': r'\bgraduation\b',
    'anniversary': r'\banniversary\b',
    'christmas': r'\bchristmas\b',
    'holiday': r'\bholidays?\b',
    'wedding': r'\bwedding\b',
    'baby shower': r'\bbaby shower\b',
}


def _question_focus(clause: str) -> str | None:
    topic = r'fabric|material|color|colour|style|fit|use case|occasion'
    clause = re.sub(r'^(?:actually|instead|on second thought)[,:]?\s*', '',
                    clause.strip(' .!?'), flags=re.I)
    match = re.fullmatch(
        rf'(?:i\s+care\s+(?:(?:most|more)\s+)?about\s+(?P<first>{topic})'
        rf'|(?P<second>{topic})\s+matters(?:\s+(?:most(?:\s+to\s+me)?|more(?:\s+(?:to\s+me|than\s+\w+))?|to\s+me))?)',
        clause, re.I)
    if not match:
        return None
    value = next(value for value in match.groupdict().values() if value).lower()
    return {'fabric': 'material', 'colour': 'color', 'fit': 'style',
            'occasion': 'use_case', 'use case': 'use_case'}.get(value, value)


def _category_alternatives(text: str) -> tuple[tuple[str, str], ...]:
    """Find linked product types and retain the words local to each item."""
    mentions = sorted(((match.start(), match.end(), category)
                       for category, phrases in CATEGORY_PATTERNS.items()
                       for phrase in phrases if phrase.isascii()
                       for match in re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I)),
                      key=lambda row: (row[0], -(row[1] - row[0])))
    distinct = []
    for mention in mentions:
        if not distinct or mention[0] >= distinct[-1][1]:
            distinct.append(mention)
    for index, first in enumerate(distinct[:-1]):
        prefix = text[max(0, first[0] - 35):first[0]]
        if re.search(r"(?:\b(?:not|no|without|avoid|excluding)\s+|\bdon't want\s+)"
                     r"(?:(?:a|an|any|the)\s+)?$", prefix, re.I):
            continue
        choices = [(first[2], text[(distinct[index - 1][1] if index else 0):first[1]])]
        previous = first
        for current in distinct[index + 1:]:
            between = text[previous[1]:current[0]]
            linked = re.fullmatch(r'\s*,?\s*(?:or|and|/)\s*(?:[a-z][a-z-]*\s+){0,3}', between, re.I)
            if not linked:
                break
            if current[2] not in {name for name, _ in choices}:
                choices.append((current[2], text[previous[1]:current[1]]))
            previous = current
        if len(choices) >= 2:
            return tuple(choices)
    return ()


def _category_after_rejection(text: str) -> tuple[bool, str | None]:
    """Return a sole affirmative type, or suppress a fully rejected choice."""
    mentions = sorted(((match.start(), match.end(), category)
                       for category, phrases in CATEGORY_PATTERNS.items()
                       for phrase in phrases if phrase.isascii()
                       for match in re.finditer(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', text, re.I)),
                      key=lambda row: (row[0], -(row[1] - row[0])))
    distinct = []
    for mention in mentions:
        if not distinct or mention[0] >= distinct[-1][1]:
            distinct.append(mention)
    positive = set()
    rejected = False
    prior_end = None
    prior_negated = False
    for start, end, category in distinct:
        prefix = text[max(0, start - 60):start]
        locally_negated = re.search(
            r"(?:\b(?:not|no|without|avoid|excluding|forget|drop|remove|instead of)\s+|"
            r"\bdon't want\s+)"
            r"(?:(?:a|an|any|the)\s+)?(?:[a-z][a-z-]*\s+){0,3}$",
            prefix, re.I)
        linked_rejection = (prior_negated and prior_end is not None and
                            re.fullmatch(r'\s+(?:or|and)\s+(?:(?:a|an|the)\s+)?',
                                         text[prior_end:start], re.I))
        negated = bool(locally_negated or linked_rejection)
        if negated:
            rejected = True
        else:
            positive.add(category)
        prior_end, prior_negated = end, negated
    return rejected, next(iter(positive)) if rejected and len(positive) == 1 else None


def _selected_category_details(pending: dict, category: str, message: str,
                               explicit_updates=(), deferred_details=None) -> tuple[SlotUpdate, ...]:
    if deferred_details is not None and category in deferred_details:
        scoped = deferred_details
    elif pending.get('reason') == 'choose_category_alternative':
        scoped = pending.get('scoped_details') or {}
    else:
        return ()
    details = scoped.get(category, {})
    explicit_slots = {update.slot for update in explicit_updates}
    selected = []
    for slot, detail in details.items():
        target_slot = slot.removesuffix('_exclude')
        if target_slot in explicit_slots:
            continue
        values = tuple(detail['values'])
        if slot.endswith('_exclude'):
            selected.append(SlotUpdate(target_slot, 'exclude', values, evidence=detail.get('evidence', message)))
            continue
        selected.append(SlotUpdate(slot, 'remove_exclusion', values, evidence=message))
        selected.append(SlotUpdate(slot, 'set', values, detail['constraint_type'], 1.0,
                                   detail.get('evidence', message)))
    return tuple(selected)


class TurnIntentRouter(IntentRouter):
    def understand_turn(self, message: str, *, pending_question: dict | None = None,
                        deferred_category_details: dict | None = None,
                        active_category: str | None = None):
        parsed = self.understand(message)
        text = parsed.normalized_query
        pending = pending_question or {}
        updates = []
        search_only_reset = re.fullmatch(
            r"reset (?:the )?search only|clear (?:my |the )?search (?:requirements|preferences|filters) only",
            text.strip(' .!?'), re.I,
        )
        everything_reset = re.fullmatch(r'(?:reset|clear) everything', text.strip(' .!?'), re.I)
        if re.fullmatch(
            r"(?:let'?s|let us)?\s*start over|start a new search|"
            r"clear (?:all )?(?:my |the )?search (?:requirements|preferences|filters)",
            text.strip(' .!?'), re.I,
        ) or search_only_reset or everything_reset:
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={},
                           slot_updates=(SlotUpdate('all_requirements', 'clear', evidence=message),),
                           decision_evidence={**parsed.decision_evidence, 'search_reset': True,
                                              'search_reset_scope': ('search_only' if search_only_reset else
                                                                     'everything' if everything_reset else 'fresh')})
        if re.fullmatch(r"(?:actually[, ]+)?(?:i (?:just )?want to (?:browse|look around)|"
                        r"(?:let's|let us) just (?:browse|look around)|"
                        r"i(?:'m| am) just browsing)(?: for now)?[.!?]*", text):
            return replace(parsed, intent_type='browsing', slots={}, hard_constraints={},
                           soft_preferences={}, filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence,
                                              'conversation_act': 'browse_again'})
        correction_marker = re.search(r'\b(?:actually|on second thought)\b[:,]?\s*', text)
        correction_tail = text[correction_marker.end():] if correction_marker else ''
        instead_of = re.search(r'\binstead of\b', correction_tail or text)
        if instead_of and correction_tail:
            correction_tail = correction_tail[:instead_of.start()].strip()
        elif instead_of:
            earlier_clauses = [part.strip() for part in re.split(r'[,;]', text[:instead_of.start()])
                               if part.strip()]
            correction_tail = earlier_clauses[-1] if earlier_clauses else ''
        if not correction_tail:
            correction_tail = next((part.strip() for part in re.split(r'[,;]', text)[1:]
                                    if re.search(r'\binstead\b(?!\s+of)', part)), '')
        corrected_slots = self.understand(correction_tail).slots if correction_tail else {}
        deferred = deferred_category_details or {}
        named_deferred = [name for name in parsed.slots.get('category', ()) if name in deferred]
        deferred_clears = []
        for label, slot in (('color', 'color'), ('material', 'material'),
                            ('fabric', 'material'), ('size', 'size'),
                            ('fit', 'style'), ('style', 'style')):
            if re.search(rf'\b(?:any|no preference (?:for|on|about)|don\'t care (?:about|which))\s+'
                         rf'{label}\b|\bno\s+{label}\s+preference\b|'
                         rf'\b{label}\s+(?:doesn\'t matter|is (?:fine|open|optional))\b|'
                         rf'\b(?:remove|forget|drop)\s+(?:(?:the|my)\s+)?{label}\b', text):
                deferred_clears.append(slot)
        deferred_clears = tuple(dict.fromkeys(deferred_clears))
        exception_tail = re.search(r'\b(?:except|but not)\b([^.;]*)', text)
        excluded_colors = (tuple(color for color in COLORS
                                 if re.search(r'\b' + re.escape(color) + r'\b', exception_tail.group(1)))
                           if exception_tail and 'color' in deferred_clears else ())
        edit_cue = re.match(r'^(?:actually[, ]+)?(?:make|change)\s+(?:(?:the|my)\s+)?', text)
        edit_cue = edit_cue or re.match(r'^(?:actually[, ]+)?for\s+(?:the|my)\s+', text)
        edit_cue = edit_cue or re.match(r"^(?:actually[, ]+)?(?:(?:i(?: would|'d)? )?prefer|"
                                      r"i'd rather have)\b", text)
        if (len(named_deferred) == 1 and named_deferred[0] != active_category
                and (edit_cue or deferred_clears)):
            item = named_deferred[0]
            preference_cue = bool(re.search(r'\bprefer\b|\brather\b', text))
            details = {slot: {'values': tuple(parsed.slots[slot]),
                              'constraint_type': 'soft' if preference_cue else 'hard',
                              'evidence': message}
                       for slot in ('color', 'material', 'size', 'style')
                       if parsed.slots.get(slot) and slot not in deferred_clears}
            if excluded_colors:
                details['color_exclude'] = {'values': excluded_colors,
                                            'constraint_type': 'exclude',
                                            'evidence': message}
            shared_price = bool(re.search(r'\b(?:both|all)\b', text))
            if not shared_price:
                for name, slot in (('budget_min', 'price_min'), ('budget_max', 'price_max')):
                    if parsed.slots.get(name) is not None:
                        details[slot] = {'values': (float(parsed.slots[name]),),
                                         'constraint_type': 'hard'}
            if details or deferred_clears:
                prior_slots = set(details) | set(deferred_clears)
                prior_slots.update(slot + '_exclude' for slot in deferred_clears)
                previous = {slot: tuple(deferred[item].get(slot, {}).get('values', ()))
                            for slot in prior_slots}
                shared_updates = ()
                if shared_price:
                    shared_updates = tuple(
                        update for name, slot, tier in (('budget_min', 'price_min', 'hard'),
                                                        ('budget_max', 'price_max', 'hard'),
                                                        ('budget_target', 'budget_target', 'soft'))
                        if parsed.slots.get(name) is not None
                        for update in (SlotUpdate(slot, 'remove_exclusion',
                                                  (float(parsed.slots[name]),), evidence=message),
                                       SlotUpdate(slot, 'set', (float(parsed.slots[name]),),
                                                  tier, 0.9, message)))
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=shared_updates,
                               decision_evidence={**parsed.decision_evidence,
                                                  'conversation_act': 'deferred_item_edit',
                                                  'deferred_item_edit': {'category': item,
                                                                         'details': details,
                                                                         'remove': deferred_clears,
                                                                         'previous': previous,
                                                                         'shared_updates': tuple(update.slot for update in
                                                                                                 shared_updates if update.operation == 'set')}})
        category_items = (() if len(corrected_slots.get('category', ())) == 1 else
                          _category_alternatives(text))
        category_alternatives = tuple(name for name, _ in category_items)
        category_scoped_details = {}
        for name, fragment in category_items:
            item_updates = self.understand_turn(fragment).slot_updates or ()
            details = {update.slot: {'values': update.values,
                                     'constraint_type': update.constraint_type,
                                     'evidence': update.evidence}
                       for update in item_updates
                       if update.slot in {'color', 'material', 'size', 'style', 'brand', 'subtype'}
                       and update.operation == 'set'}
            category_scoped_details[name] = details
        if pending.get('reason') == 'unverifiable_budget':
            preview = re.fullmatch(
                r"(?:yes,?\s*|please\s+)?(?:"
                r"(?:show(?: me)?|let me see|i(?:'d| would) like to see)\s+(?:the\s+)?"
                r"(?:unpriced\s+)?(?:ideas|options|products)(?:\s+(?:anyway|without prices))?"
                r"|show(?: me)?\s+(?:them\s+)?anyway)"
                r"(?:\s*[,;]\s*(?P<extra>.+))?[.!?]*", text)
            if preview:
                cap = pending.get('budget_cap')
                floor = pending.get('budget_floor')
                changes = [SlotUpdate(name, 'clear', evidence=message)
                           for name, bound in (('price_min', floor), ('price_max', cap))
                           if bound is not None]
                if floor is not None and not pending.get('budget_floor_target_existing'):
                    changes.append(SlotUpdate('budget_floor_target', 'set', (float(floor),), 'soft', 1.0, message))
                if cap is not None and not pending.get('budget_target_existing'):
                    changes.append(SlotUpdate('budget_target', 'set', (float(cap),), 'soft', 1.0, message))
                extra = self.understand_turn(preview.group('extra')) if preview.group('extra') else None
                if extra:
                    changes.extend(extra.slot_updates or ())
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=tuple(changes),
                               decision_evidence={**parsed.decision_evidence, 'requested_results': True})
            if re.fullmatch(r"(?:no,?\s*)?(?:(?:keep|stay with)\s+(?:the\s+)?strict\s+(?:budget|limit)|keep it strict)[.!?]*", text):
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=(),
                               decision_evidence={**parsed.decision_evidence,
                                                  'conversation_act': 'budget_strict'})
        priority_topic = r'fabric|material|color|colour|style|fit|use case|occasion'
        priority_shift = re.fullmatch(
            rf'(?:(?:actually|instead|on second thought)[,:]?\s*)?'
            rf'(?:(?P<focus>{priority_topic})\s+matters\s+more\s+than\s+(?P<prior>{priority_topic})'
            rf'|i\s+care\s+more\s+about\s+(?P<focus_alt>{priority_topic})\s+than\s+(?P<prior_alt>{priority_topic}))',
            text.strip(' .!?'), re.I)
        if priority_shift:
            focus = _question_focus(f"{priority_shift.group('focus') or priority_shift.group('focus_alt')} matters")
            prior = _question_focus(f"{priority_shift.group('prior') or priority_shift.group('prior_alt')} matters")
            if focus and prior and focus != prior:
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={},
                               slot_updates=(SlotUpdate(prior, 'demote_soft', evidence=message),
                                             SlotUpdate(focus, 'promote_soft', evidence=message)),
                               decision_evidence={**parsed.decision_evidence,
                                                  'question_focus': focus,
                                                  'preference_priority_shift': {'from': prior, 'to': focus}})
        focus_parts = [part.strip() for part in re.split(
            r'\s*[,;]\s*(?:(?:and|but)\s+)?|\s+but\s+', text) if part.strip()]
        focused = [(index, _question_focus(part)) for index, part in enumerate(focus_parts)]
        focused = [(index, name) for index, name in focused if name]
        if len(focused) == 1:
            index, name = focused[0]
            remaining = ', '.join(part for i, part in enumerate(focus_parts) if i != index)
            extra = self.understand_turn(remaining, pending_question=pending) if remaining else None
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=extra.slot_updates if extra else (),
                           decision_evidence={**parsed.decision_evidence,
                                              **(extra.decision_evidence if extra else {}),
                                              'question_focus': name})
        if (pending.get('reason') == 'choose_category_alternative' and
                (re.fullmatch(r'(?:show me )?(?:both|all(?: of them)?|either one)', text.strip(' .!?'))
                 or re.fullmatch(OPTION_ACCEPTANCE, text.strip(' .!?')))):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence,
                                              'conversation_act': 'category_both'})
        if (pending.get('target_slot') == 'category' and re.fullmatch(
                r'(?:whatever|anything|anything is fine|surprise me|you pick)[.!?]*', text)):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence,
                                              'conversation_act': 'unsure_category'})
        decline = (re.fullmatch(
            r'(?:neither(?: one| of those)?|no preference(?: between them)?|'
            + OPTION_ACCEPTANCE + r')'
            r'(?:(?:\s*,\s*(?:(?:but|and)\s+)?|\s+(?:but|and)\s+)(?P<extra>.+))?[.!?]*', text)
                   if pending.get('options') else None)
        if decline:
            accepted_options = bool(re.match(OPTION_ACCEPTANCE, text))
            extra = self.understand_turn(decline.group('extra')) if decline.group('extra') else None
            if extra and (extra.slot_updates or extra.decision_evidence.get('requested_results') or
                          extra.decision_evidence.get('requirement_control')):
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=extra.slot_updates,
                               decision_evidence={**parsed.decision_evidence, **extra.decision_evidence,
                                                  'requested_results': True, 'declined_options': True,
                                                  'accepted_options': accepted_options})
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence,
                                              'conversation_act': 'declined_options',
                                              'declined_options': True,
                                              'accepted_options': accepted_options})
        if re.fullmatch(r'(?:please\s+)?(?:retry|try again|search again|重试|再试一次|再搜一次|重新搜索)[.!?。！？]*', text):
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=(),
                           decision_evidence={**parsed.decision_evidence, 'requested_results': True,
                                              'retry_search': True})
        # Resolve an exact displayed option using the current question only.
        # Keep free-form/multi-detail replies on the normal extraction path.
        from .option_labels import OPTION_LABELS_ZH
        for value, label in pending.get('option_labels', {}).items():
            if text.rstrip(' .!?。！？') in {value.casefold(), label.casefold(), OPTION_LABELS_ZH.get(value, value)} and value in pending.get('options', ()):
                chosen = SlotUpdate(pending['target_slot'], 'set',
                                    (value,), pending.get('constraint_type', 'soft'), 1.0, message)
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=(chosen, *_selected_category_details(pending, value, message)))
        # A shopper may choose a displayed option and volunteer another detail
        # in the same reply. Match an entire clause, never a word inside a
        # longer phrase, and leave the other clauses to ordinary extraction.
        if pending.get('option_labels'):
            clauses = [part.strip() for part in re.split(
                r'\s*[,，;；。.!?！？]\s*|\s+(?:and|but)\s+|(?:但是|但|另外)', text) if part.strip()]
            matches = [(index, value) for index, clause in enumerate(clauses)
                       for value, label in pending['option_labels'].items()
                       if value in pending.get('options', ())
                       and clause in {value.casefold(), label.casefold(), OPTION_LABELS_ZH.get(value, value)}]
            if len(matches) == 1 and len(clauses) > 1:
                index, value = matches[0]
                extra = self.understand_turn(', '.join(clause for i, clause in enumerate(clauses) if i != index))
                if not any(update.slot == pending['target_slot'] for update in extra.slot_updates or ()):
                    chosen = SlotUpdate(pending['target_slot'], 'set', (value,),
                                        pending.get('constraint_type', 'soft'), 1.0, message)
                    return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                                   filter_constraints={}, slot_updates=(chosen, *_selected_category_details(
                                       pending, value, message, extra.slot_updates or ()), *(extra.slot_updates or ())),
                                   decision_evidence={**parsed.decision_evidence, **extra.decision_evidence})
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

        lift_exclusion = re.fullmatch(
            r"(?:(?:i\s+)?no longer (?:want to )?(?:avoid|exclude)|"
            r"(?:please\s+)?stop (?:avoiding|excluding)|"
            r"(?:please\s+)?(?:don't|do not) (?:avoid|exclude))\s+(.+?)[.!?]*",
            text,
        )
        if lift_exclusion:
            value_text = lift_exclusion.group(1).strip()
            values = self.understand(value_text).slots
            lifted = []
            for slot in ('color', 'style', 'material'):
                for value in values.get(slot, ()):
                    if slot == 'material' and value == 'cotton' and re.search(
                        r'\b(?:100\s*%\s*|pure\s+|all[ -])cotton\b', value_text):
                        value = '100% cotton'
                    lifted.append(SlotUpdate(slot, 'remove_exclusion', (value,), None, 0.9, message))
            if lifted:
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=tuple(lifted),
                               decision_evidence={**parsed.decision_evidence, 'requested_results': True})

        withdraw_price = re.fullmatch(
            r"(?:please\s+)?(?:remove|drop|forget|clear)\s+(?:(?:the|my)\s+)?"
            r"(?:(?:about|around|under|up to)\s+)?(?:\$\s*\d+(?:\.\d+)?\s+)?"
            r"(?P<kind>budget|spending limit|price (?:limit|cap|ceiling|floor|minimum|target)|"
            r"minimum price|target price)"
            r"(?P<tail>(?:\s*,\s*|\s+(?:and|but)\s+).+)?[.!?]*",
            text,
        )
        if withdraw_price:
            kind = withdraw_price.group('kind')
            slots = (('price_min', 'price_max', 'budget_target', 'budget_floor_target') if kind == 'budget' else
                     ('price_min', 'budget_floor_target') if kind in {'minimum price', 'price minimum', 'price floor'} else
                     ('budget_target',) if kind in {'target price', 'price target'} else
                     ('price_max',))
            changes = [SlotUpdate(slot, 'clear', (), None, 0.9, message) for slot in slots]
            tail = withdraw_price.group('tail')
            extra = None
            if tail:
                remaining = re.sub(r'^\s*(?:,\s*(?:but|and)?\s*|(?:and|but)\s+)', '', tail)
                extra = self.understand_turn(remaining) if remaining else None
                if extra:
                    changes.extend(extra.slot_updates or ())
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=tuple(changes),
                           decision_evidence={**parsed.decision_evidence,
                                              **(extra.decision_evidence if extra else {}),
                                              'requested_results': True})

        withdraw = re.fullmatch(
            r"(?:please\s+)?(?:remove|drop|forget)\s+(?:(?:the|my)\s+)?"
            r"(?P<value>.+?)\s+(?:requirement|preference|constraint)"
            r"(?P<tail>(?:\s*,\s*|\s+(?:and|but)\s+).+)?[.!?]*",
            text,
        )
        if withdraw:
            named = self.understand(withdraw.group('value')).slots
            candidates = [(slot, value) for slot in ('color', 'material', 'style', 'size', 'brand')
                          for value in named.get(slot, ())]
            if len(candidates) == 1:
                slot, value = candidates[0]
                changes = [SlotUpdate(slot, 'remove_value', (value,), None, 0.9, message)]
                tail = withdraw.group('tail')
                extra = None
                if tail:
                    remaining = re.sub(r'^\s*(?:,\s*(?:but|and)?\s*|(?:and|but)\s+)', '', tail)
                    extra = self.understand_turn(remaining) if remaining else None
                    if extra:
                        changes.extend(extra.slot_updates or ())
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=tuple(changes),
                               decision_evidence={**parsed.decision_evidence,
                                                  **(extra.decision_evidence if extra else {}),
                                                  'requested_results': True})

        indifferent = re.fullmatch(
            r"(?:i\s+)?(?:don't|do not)\s+care\s+about\s+(?:(?:the|my)\s+)?"
            r"(?P<dimension>colou?r|fit|style|material|fabric|brand|size|budget|price|occasion|use case)"
            r"(?:\s+anymore)?(?P<tail>(?:\s*,\s*|\s+(?:and|but)\s+).+)?[.!?]*",
            text,
        )
        if indifferent:
            dimension = indifferent.group('dimension')
            slot = {'colour': 'color', 'fit': 'style', 'fabric': 'material',
                    'occasion': 'use_case', 'use case': 'use_case'}.get(dimension, dimension)
            slots = ('price_min', 'price_max', 'budget_target', 'budget_floor_target') if slot in {'budget', 'price'} else (slot,)
            changes = [SlotUpdate(name, 'clear', (), None, 0.9, message) for name in slots]
            tail = indifferent.group('tail')
            extra = None
            if tail:
                remaining = re.sub(r'^\s*(?:,\s*(?:but|and)?\s*|(?:and|but)\s+)', '', tail)
                extra = self.understand_turn(remaining) if remaining else None
                if extra:
                    changes.extend(extra.slot_updates or ())
            return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                           filter_constraints={}, slot_updates=tuple(changes),
                           decision_evidence={**parsed.decision_evidence,
                                              **(extra.decision_evidence if extra else {}),
                                              'requested_results': True})

        value_indifferent = re.fullmatch(
            r"(?:i\s+)?(?:don't|do not)\s+care\s+about\s+(?:(?:the|my)\s+)?"
            r"(?P<value>.+?)(?:\s+anymore)?"
            r"(?P<tail>(?:\s*,\s*|\s+(?:and|but)\s+).+)?[.!?]*",
            text,
        )
        if value_indifferent:
            value_text = value_indifferent.group('value').strip()
            named = self.understand(value_text).slots
            candidates = [(slot, value) for slot in ('color', 'material', 'style', 'size', 'brand')
                          for value in named.get(slot, ()) if value_text == value]
            if len(candidates) == 1:
                slot, value = candidates[0]
                changes = [SlotUpdate(slot, 'remove_value', (value,), None, 0.9, message)]
                tail = value_indifferent.group('tail')
                extra = None
                if tail:
                    remaining = re.sub(r'^\s*(?:,\s*(?:but|and)?\s*|(?:and|but)\s+)', '', tail)
                    extra = self.understand_turn(remaining) if remaining else None
                    if extra:
                        changes.extend(extra.slot_updates or ())
                return replace(parsed, intent_type=None, slots={}, hard_constraints={}, soft_preferences={},
                               filter_constraints={}, slot_updates=tuple(changes),
                               decision_evidence={**parsed.decision_evidence,
                                                  **(extra.decision_evidence if extra else {}),
                                                  'requested_results': True})

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
            for name in (("price_min", "price_max", "budget_target", "budget_floor_target") if slot == "budget" else (slot,)):
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
        if len(corrected_slots.get('category', ())) == 1:
            category = corrected_slots['category'][0]
        category_rejection, corrected_category = _category_after_rejection(text)
        if category_rejection:
            category = corrected_category
        if category_alternatives:
            category = None
        direct = bool(re.search(r"\b(?:need|want|show me|find|looking for|switch to|change to)\b|(?:我需要|我想要|帮我找|给我找|换成|改成|找一?[个件双条款]?)", text))
        if not category and not category_alternatives and not category_rejection:
            for name, phrases in CATEGORY_PATTERNS.items():
                if any(_contains(text, phrase) for phrase in phrases):
                    if direct or pending.get("target_slot") == "category" or len(text.split()) <= 4 or len(recognized) == 1:
                        category = name
                        break
        if category:
            add("category", "set", (category,), "hard")
            if category == 'pants' and re.search(r'\bjeans\b|\bjean\s+(?:pants?|trousers?)\b', category_text):
                add('subtype', 'set', ('jeans',), 'hard')
            elif category == 'pants' and re.search(r'\b(?:pants|trousers|leggings)\b', category_text):
                add('subtype', 'clear')

        # A gift is a shopping purpose, not a searchable product category.
        # Keep the stated recipient so a later product-type answer does not
        # erase the context supplied in the opening turn.
        gift = re.search(
            r"\b(?P<noun>gift|present)\b(?:\s+for\s+(?:(?:my|a|the)\s+)?"
            rf"(?P<recipient>{GIFT_RECIPIENTS}))?\b", text)
        # "A T-shirt for my sister's birthday" names a recipient and an
        # occasion without saying "gift". Do not make the same inference for
        # weddings, where the shopper may be buying their own outfit.
        recipient_occasion = re.search(
            rf"\bfor\s+(?:(?:my|our|a|the)\s+)?(?P<recipient>{GIFT_RECIPIENTS})"
            r"(?:'s|’s)\s+(?P<occasion>birthday|graduation|christmas|holiday)\b",
            text)
        recipient_pattern = rf"\bfor\s+(?:(?:my|a|the)\s+)?({GIFT_RECIPIENTS})\b"
        if ((gift or recipient_occasion) and not re.search(
                r"\b(?:not|no|without)\s+(?:a\s+)?(?:gift|present)\b|\b(?:gift|present)\s+card\b", text)):
            corrected_recipient = re.search(recipient_pattern, correction_tail) if correction_tail else None
            corrected_occasion_recipient = re.search(
                rf"\bfor\s+(?:(?:my|our|a|the)\s+)?({GIFT_RECIPIENTS})"
                r"(?:'s|’s)\s+(?:birthday|graduation|christmas|holiday)\b",
                correction_tail) if correction_tail else None
            recipient = (corrected_occasion_recipient.group(1) if corrected_occasion_recipient else
                         corrected_recipient.group(1) if corrected_recipient else
                         gift.group('recipient') if gift and gift.group('recipient') else
                         recipient_occasion.group('recipient') if recipient_occasion else None)
            purpose = 'gift' + (f" for {recipient}" if recipient else '')
            add('shopping_purpose', 'set', (purpose,), 'soft')
        elif pending.get('gift_context'):
            corrected_recipient = re.search(recipient_pattern, text)
            if corrected_recipient:
                add('shopping_purpose', 'set', (f'gift for {corrected_recipient.group(1)}',), 'soft')
        if re.search(r"\b(?:not a gift anymore|no longer a gift|forget (?:the|my) gift)\b", text):
            add('shopping_purpose', 'clear')
        occasion_cue = re.search(
            r"\b(?:for|at|to celebrate)\s+(?:(?:my|her|his|their|a|the)\s+)?"
            r"(?:birthday|graduation|anniversary|christmas|holiday|wedding|baby shower)\b",
            text)
        if gift or recipient_occasion or occasion_cue:
            occasion_text = (correction_tail if correction_tail and any(
                re.search(pattern, correction_tail) for pattern in SHOPPING_OCCASIONS.values())
                else text)
            occasions = tuple(name for name, pattern in SHOPPING_OCCASIONS.items()
                              if re.search(pattern, occasion_text))
            if occasions:
                add('shopping_occasion', 'set', occasions, 'soft')
        if re.search(r"\b(?:no occasion|not for (?:a|any) occasion|just because|forget (?:the|my) occasion)\b", text):
            add('shopping_occasion', 'clear')

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
            if name in corrected_slots and name in {
                    'color', 'material', 'size', 'brand', 'style', 'use_case',
                    'budget_min', 'budget_max', 'budget_target'}:
                raw = corrected_slots[name]
            if name in {"category", "audience"} or name.endswith("_exclude"):
                continue
            if name not in {"color", "material", "size", "brand", "style", "use_case", "feature", "budget_min", "budget_max", "budget_target"}:
                continue
            if name in cleared or (name.startswith("budget_") and "budget" in cleared):
                continue
            values = raw if isinstance(raw, list) else [raw]
            if (name == 'use_case' and 'work' in values and
                    NEGATIVE_BATCH_RE.search(text) and not re.search(r'\bfor work\b', text)):
                values = [value for value in values if value != 'work']
            if name == 'use_case' and gift and 'wedding' in values and (
                    not category or re.search(
                        r'\bwedding\s+(?:gift|present)\b|'
                        r'\b(?:gift|present)\s+(?:(?:for|at)\s+)?(?:a\s+)?wedding\b', text)):
                values = [value for value in values if value != 'wedding']
            rejected = scoped.slots.get(name + "_exclude", [])
            values = [v for v in values if v not in rejected]
            if not values:
                continue
            # Catalog stores include ordinary words such as 'switch' and 'not'.
            # Require an explicit brand cue instead of making those hard filters.
            if name == "brand" and not (pending.get("target_slot") == "brand" or re.search(r"\b(?:brand|by|from)\b", scoped_text)):
                continue
            ordered_color = (name == 'color' and len(values) == 2 and
                             re.search(r'\b(?:prefer|preferred|first choice|first|priority)\b', text) and
                             re.search(r'\b(?:okay|ok|fine|acceptable)\b', text) and
                             not re.search(r'\bnot\s+(?:okay|ok|fine|acceptable)\b', text))
            if name == "color" and (ordered_color or re.search(r"\b(?:also|too)\b.*\b(?:fine|okay|ok)\b|\bis\s+(?:also\s+)?(?:fine|okay|ok)\b", text)):
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
            soft = name in {"style", "use_case", "feature", "budget_target"} or bool(re.search(
                r"\b(?:prefer|preferably|maybe|ideally|like|perhaps|probably|possibly|"
                r"not sure|i think|i guess|i might|might)\b|"
                r"\b(?:would be (?:nice|good)|nice to have)\b|"
                r"(?:最好|偏好|希望|尽量|也不错|也可以|或许|可能)", value_clause))
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
        # Degree language is a soft ranking preference, not an absolute ban.
        # Keep it distinct from positive style and from hard exclusions.
        if re.search(r"\b(?:not|isn['’]?t|aren['’]?t|nothing)\s+too\s+baggy\b", text):
            add('fit_avoid', 'set', ('baggy',), 'soft')
        if re.search(r"\b(?:baggy\s+is\s+(?:fine|okay|ok)|"
                     r"i\s+(?:don't|do not)\s+mind\s+baggy|"
                     r"forget\s+(?:the\s+)?not.too.baggy\s+preference)\b", text):
            add('fit_avoid', 'clear')
        # Comparative fit requests are preference updates, not a reason to
        # repeat the same search with no new requirements. Keep them soft:
        # a previously hard fit will go through the normal conflict question.
        if not any(update.slot == 'style' and update.operation == 'set' for update in updates):
            clauses = re.split(r'[,，;；.!?。！？]|\s+and\s+|\s+but\s+', text)
            for clause in clauses:
                phrase = clause.strip()
                relative_fit = re.fullmatch(
                    r"(?:(?:actually|instead)\s+)?(?:(?:i(?:'d| would)?\s+(?:like|prefer|want)|"
                    r"(?:can you )?(?:show|find) me)\s+)?(?:something|a|one|some|options?)?\s*"
                    r"(?:(?:a bit|much)\s+)?(?P<direction>looser|less fitted|more relaxed|roomier|"
                    r"more fitted|slimmer)(?:\s+(?:fit|one|options?))?(?:\s+please)?",
                    phrase, re.I,
                )
                if relative_fit:
                    style = ('slim fit' if relative_fit.group('direction') in {'more fitted', 'slimmer'}
                             else 'loose fit')
                    if re.search(r'\b(?:actually|instead|changed my mind)\b', text, re.I):
                        add('style', 'clear')
                    add('style', 'remove_exclusion', (style,))
                    add('style', 'set', (style,), 'soft')
                    break
        # Short answers to a structured question need not repeat the slot name.
        if (not updates and pending and not pending.get('options') and not correction
                and len(text.split()) <= 5 and text.strip(" .") and not RESULT_CONTROL_RE.fullmatch(text)):
            target = pending["target_slot"]
            if target in {"category", "color", "material", "brand", "size", "style", "use_case"}:
                add(target, "set", (text.strip(" ."),), "hard" if target == "category" else pending.get("constraint_type", "soft"))
            elif target in {"feature", "other"}:
                add(feature_slot(text.strip(" .")), "set", (text.strip(" ."),), pending.get("constraint_type", "soft"))
            elif target == "budget" and re.search(r"\d", text):
                number = float(re.search(r"\d+(?:\.\d+)?", text).group())
                add("budget_max", "set", (number,), "hard")
        more_requested = any(re.fullmatch(r'(?:show|give|find)(?: me)? more(?: options|products|results)?', part.strip())
                             for part in re.split(r'[,;.]', text))
        evidence = {**parsed.decision_evidence,
                    "negative_feedback": bool(NEGATIVE_BATCH_RE.search(text)) or "not quite right" in text or "none of these" in text or "不太合适" in text or "这些都不行" in text}
        evidence['browse_first'] = bool(re.search(
            r'\b(?:browsing|exploring|looking around|just looking|looking for ideas|'
            r'for ideas|for inspiration|to get ideas)\b', text))
        evidence['decision_help'] = bool(re.search(
            r'\b(?:help me (?:choose|decide|pick)|ready to (?:choose|decide))\b', text))
        if category_alternatives:
            evidence['category_alternatives'] = category_alternatives
            evidence['category_scoped_details'] = category_scoped_details
            scoped_slots = {slot for details in category_scoped_details.values() for slot in details}
            updates = [update for update in updates
                       if not (update.slot in scoped_slots and update.operation in {'set', 'remove_exclusion'})]
        if pending.get('reason') == 'choose_category_alternative' and not category_alternatives:
            chosen_categories = [update.values[0] for update in updates
                                 if update.slot == 'category' and update.operation == 'set'
                                 and len(update.values) == 1
                                 and update.values[0] in pending.get('options', ())]
            if len(chosen_categories) == 1:
                updates.extend(_selected_category_details(pending, chosen_categories[0], message, updates,
                                                         deferred_category_details))
        elif deferred_category_details and not category_alternatives:
            chosen_categories = [update.values[0] for update in updates
                                 if update.slot == 'category' and update.operation == 'set'
                                 and len(update.values) == 1
                                 and update.values[0] in deferred_category_details]
            if len(chosen_categories) == 1:
                resumed = _selected_category_details({}, chosen_categories[0], message, updates,
                                                     deferred_category_details)
                if resumed:
                    updates.extend(resumed)
                    evidence['resumed_category_details'] = chosen_categories[0]
        if ((pending.get('evidence') or {}).get('expected_reduction') is not None
                and updates and not any(update.slot == pending.get('target_slot') for update in updates)
                and not any(update.slot == 'category' for update in updates)
                and not evidence['negative_feedback']):
            evidence['bypassed_optional_question'] = pending.get('target_slot')
        if more_requested:
            evidence.update(requested_results=True, requested_more=True)
        if (pending.get('options') and not updates and not evidence['negative_feedback']
                and not evidence.get('requested_results') and len(text.split()) <= 5):
            evidence['conversation_act'] = 'unmatched_option'
        # Preserve the stronger meaning instead of collapsing pure cotton into
        # any cotton content. The same value works for preferences/exclusions.
        if re.search(r'纯棉|\b(?:100\s*%\s*|pure\s+|all[ -])cotton\b', text):
            updates = [replace(update, values=tuple('100% cotton' if value == 'cotton' else value for value in update.values))
                       if update.slot == 'material' else update for update in updates]
        if category_alternatives:
            slots = {name: value for name, value in parsed.slots.items() if name != 'category'}
            hard = {name: value for name, value in parsed.hard_constraints.items() if name != 'category'}
            return replace(parsed, slots=slots, hard_constraints=hard,
                           slot_updates=tuple(updates), decision_evidence=evidence)
        return replace(parsed, slot_updates=tuple(updates), decision_evidence=evidence)
