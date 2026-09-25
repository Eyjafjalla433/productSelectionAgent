"""Conservative chat controls for shortlist/compare actions.

These commands are handled by C's session layer. They never become shopping
requirements and never invoke retrieval or ranking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}
CONTROL_PATTERNS = {
    "clear": re.compile(r"(?:clear|empty).{0,12}(?:shortlist|selection)|清空.{0,8}(?:候选|选择|清单)", re.I),
    "finalize": re.compile(r"^(?:(?:(?:can|could|would) you )?(?:please )?(?:finali[sz]e(?: (?:my |the |our )?(?:shortlist|selection|choices?))?|(?:finish|confirm) (?:my |the |our )?(?:shortlist|selection|choices?))(?:,? please)?|(?:请)?(?:完成|确认|确定)(?:最终)?(?:选择|选型|候选))[.!?。！？]*$", re.I),
    "handoff": re.compile(r"(?:export|generate).{0,12}(?:handoff|shortlist|selection|comparison)?|(?:导出|生成).{0,8}(?:交接|选择|选型|对比)", re.I),
    "reject": re.compile(r"(?:reject|hide|don['’]?t like|do not like|not interested in).{0,20}(?:#?\d+|first|second|third|fourth|fifth)|(?:不要|不喜欢|排除|别再推荐).{0,12}(?:第)?[一二三四五六七八九十\d]+", re.I),
    "remove": re.compile(r"(?:remove|deselect|drop).{0,20}(?:#?\d+|first|second|third|fourth|fifth)|(?:移除|取消|删掉|不要).{0,12}(?:第)?[一二三四五六七八九十\d]+", re.I),
    "compare": re.compile(r"(?:compare|对比|比较)", re.I),
    "select": re.compile(r"(?:select|choose|shortlist|pick|like|keep).{0,20}(?:#?\d+|first|second|third|fourth|fifth)|(?:选择|选中|喜欢|保留|加入候选).{0,12}(?:第)?[一二三四五六七八九十\d]+", re.I),
}


@dataclass(frozen=True)
class ControlIntent:
    action: str
    ranks: tuple[int, ...] = ()
    attribute: str | None = None
    uses_focus: bool = False


def _ranks(message: str) -> tuple[int, ...]:
    lowered = message.casefold()
    found: list[int] = []
    for word, rank in ORDINALS.items():
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            found.append(rank)
    for raw in re.findall(r"(?<![$\d])#?([1-9]|10)(?!\d)", lowered):
        found.append(int(raw))
    return tuple(dict.fromkeys(found))


def parse_detail_reference(message: str, attribute: str | None) -> ControlIntent | None:
    if attribute and re.fullmatch(r'(?:#?\d+|第[一二三四五六七八九十\d]+[款个件])[。.!]*', message.strip()):
        return ControlIntent('detail', _ranks(message), attribute)
    return None


@dataclass(frozen=True)
class CompoundTurnPlan:
    details: tuple[ControlIntent, ...]
    requirement_message: str | None


def _clauses(message: str) -> list[str]:
    boundary = (
        r'(?<=[?!.;])\s+(?:(?:and|also|plus|then)\s+)?'
        r'|(?<=[?!.;])\s*(?:and|also|plus|then)\s+'
        r'|(?<=[？。；])\s*(?:另外|顺便|同时)?\s*'
        r'|[,，]\s*(?:(?:and\s+)?also|and|but|then|另外|顺便|同时)\s*[,，]?\s*'
        r'|[,，]\s*(?=(?:show|find|give|list|display|recommend)\b)'
        r'|\s+(?:and|then|but)\s+(?=(?:is|what|how|change|switch|show|find|keep|select|choose|remove|drop|reject|hide|clear|empty|finalize|compare|undo|redo|don.t|do|i|not)\b)'
    )
    return [re.sub(r'^(?:(?:also|and|plus|then|but|另外|顺便|同时)\s*[,，]?\s*|[,，]\s*)',
                   '', part.strip(), flags=re.I)
            for part in re.split(boundary, message.strip(), flags=re.I)]


def _actionable_requirements(parts: list[str]) -> str | None:
    if not parts:
        return None
    requirement_message = ', '.join(part.rstrip('.!?;。！？； ') for part in parts)
    from intent_router.turn_router import TurnIntentRouter
    parsed = TurnIntentRouter().understand_turn(requirement_message)
    actionable = (parsed.slot_updates or parsed.decision_evidence.get('requirement_control')
                  or parsed.decision_evidence.get('conversation_act')
                  or parsed.decision_evidence.get('requested_results'))
    if not actionable or len(parts) > 1 and parsed.decision_evidence.get('requirement_control'):
        return None
    return requirement_message


def plan_compound_turn(message: str) -> CompoundTurnPlan | None:
    """Plan separately phrased product questions and one requirement update."""
    parts = _clauses(message)
    if not 2 <= len(parts) <= 12 or any(not part for part in parts):
        return None
    details: list[ControlIntent] = []
    requirements: list[str] = []
    for part in parts:
        control = parse_control_intent(part)
        if control is not None and control.action == 'detail':
            details.append(control)
        elif control is not None:
            return None
        else:
            requirements.append(part)
    if not details or len(details) > 6:
        return None
    if not requirements:
        return CompoundTurnPlan(tuple(details), None) if len(details) > 1 else None
    requirement_message = _actionable_requirements(requirements)
    return CompoundTurnPlan(tuple(details), requirement_message) if requirement_message else None


def plan_selection_and_requirements(message: str) -> tuple[ControlIntent, str] | None:
    """Identify a displayed-list action plus a separate search refinement.

    The runtime decides which actions can be safely committed together. This
    prevents a broad selection regex from silently swallowing the refinement.
    """
    parts = _clauses(message)
    if not 2 <= len(parts) <= 6 or any(not part for part in parts):
        return None
    controls = [control for part in parts if (control := parse_control_intent(part))]
    if len(controls) != 1 or controls[0].action == 'detail':
        return None
    requirements = [part for part in parts if parse_control_intent(part) is None]
    message = _actionable_requirements(requirements)
    return (controls[0], message) if message else None


def plan_similarity_and_requirements(message: str) -> tuple[ControlIntent, str] | None:
    """Keep a catalog reference separate from an explicit new search constraint."""
    match = re.match(
        r'^(?P<reference>.*?(?:#(?:[1-9]|10)|\b(?:it|this one|that one)\b))'
        r'(?P<tail>\s*(?:,|\bbut\b|\band\b|\bwith\b|\bin\b).*)$',
        message.strip(), re.I,
    )
    if not match:
        return None
    control = parse_control_intent(match.group('reference').strip())
    if control is None or control.action != 'similar':
        return None
    tail = re.sub(r'^\s*,?\s*(?:(?:but|and|with)\s+)?', '', match.group('tail'), flags=re.I).strip()
    if not tail:
        return None
    from intent_router.turn_router import TurnIntentRouter
    parsed = TurnIntentRouter().understand_turn(tail)
    if (not parsed.slot_updates or any(update.slot == 'category' for update in parsed.slot_updates)
            or parsed.decision_evidence.get('requirement_control')
            or parsed.decision_evidence.get('conversation_act')):
        return None
    return control, f'{tail}, show me more'


def plan_similarity_and_cheaper(message: str) -> tuple[ControlIntent, str | None, bool, float | None] | None:
    """Bind a relative price request and optional explicit detail to one item."""
    text = re.sub(r'(?:,\s*|\s+)please$', '', message.strip().rstrip('.!?'), flags=re.I)
    match = re.fullmatch(
        r'(?P<reference>.+?)\s*(?:,?\s*(?:but|and)\s+|,\s*)'
        r'(?:(?:a bit|much)\s+)?(?:cheaper|less expensive|more affordable)'
        r'(?:\s+(?:ones|options))?'
        r'(?P<extra>\s*(?:,|\band\b|\bbut\b|\bwith\b|\bin\b)\s*.+)?',
        text, re.I,
    )
    if not match:
        return None
    control = parse_control_intent(match.group('reference').strip())
    if control is None or control.action != 'similar':
        return None
    extra = match.group('extra')
    if not extra:
        return control, None, True, None
    extra = re.sub(r'^\s*(?:,|and\b|but\b|with\b|in\b)\s*', '', extra, flags=re.I)
    from intent_router.turn_router import TurnIntentRouter
    parsed = TurnIntentRouter().understand_turn(extra)
    explicit_caps = [float(update.values[0]) for update in parsed.slot_updates
                     if update.slot == 'price_max' and update.operation == 'set' and update.values]
    price_phrases = re.findall(
        r'\b(?:under|below|less than|at most|up to|maximum of|no more than)\s*\$?\s*\d+(?:\.\d+)?',
        extra, re.I)
    safe = (bool(parsed.slot_updates)
            and not any(update.slot in {'category', 'price_min', 'budget_max',
                                        'budget_min', 'budget_target'}
                        for update in parsed.slot_updates)
            and len(explicit_caps) <= 1 and len(price_phrases) == len(explicit_caps)
            and not parsed.decision_evidence.get('requirement_control')
            and not parsed.decision_evidence.get('conversation_act')
            and not parse_control_intent(extra))
    return control, extra, safe, explicit_caps[0] if explicit_caps else None


def plan_shortlist_actions(message: str) -> tuple[ControlIntent, ...] | None:
    """Bind each select/remove/reject verb to only its own displayed ranks.

    A bare "not #3" after a positive choice means leave #3 off the shortlist,
    not reject the product from future search results.
    """
    parts = _clauses(message)
    if not 2 <= len(parts) <= 6 or any(not part for part in parts):
        return None
    actions: list[ControlIntent] = []
    for part in parts:
        if actions and actions[-1].action == 'select' and re.fullmatch(r'not\s+#?(?:[1-9]|10)[.!?]*', part, re.I):
            action = ControlIntent('remove', _ranks(part))
        else:
            action = parse_control_intent(part)
        if action is None or action.action not in {'select', 'remove', 'reject'} or not action.ranks:
            return None
        actions.append(action)
    return tuple(actions)


def plan_rejection_and_similarity(message: str) -> tuple[ControlIntent, ControlIntent] | None:
    """Bind a rejection and a different 'more like' reference to their own ranks."""
    parts = _clauses(message)
    if len(parts) != 2 or not all(parts):
        return None
    actions = [parse_control_intent(part) for part in parts]
    reject = next((action for action in actions if action and action.action == 'reject'), None)
    similar = next((action for action in actions if action and action.action == 'similar'), None)
    if not reject or not similar or not reject.ranks or len(similar.ranks) != 1:
        return None
    return reject, similar


def has_unhandled_shortlist_mix(message: str) -> bool:
    """Stop broad single-command regexes from swallowing a second action."""
    parts = _clauses(message)
    if not 2 <= len(parts) <= 6 or any(not part for part in parts):
        return False
    controls = [parse_control_intent(part) for part in parts]
    return (any(control and control.action not in {'detail'} for control in controls)
            and sum(bool(control) or bool(re.search(r'#(?:[1-9]|10)\b', part))
                    for control, part in zip(controls, parts)) > 1)


def split_detail_and_requirements(message: str):
    """Compatibility adapter for callers expecting exactly one detail/update."""
    plan = plan_compound_turn(message)
    if plan and len(plan.details) == 1 and plan.requirement_message is not None:
        return plan.details[0], plan.requirement_message
    return None


def parse_control_intent(message: str) -> ControlIntent | None:
    text = " ".join(message.strip().split())
    if not text:
        return None
    if re.fullmatch(r'(?:no thanks|keep browsing)[.!?]*', text, re.I):
        return ControlIntent('decline')
    if re.fullmatch(
        r"(?:what (?:are|were) my (?:current )?(?:preferences|requirements)|"
        r"what (?:have|did) i (?:tell|told) you(?: so far)?|"
        r"what (?:do|are) you (?:remember|using) about (?:me|my preferences)|"
        r"what do you know about me|"
        r"what (?:information|details) do you (?:have|keep|remember) about me|"
        r"summari[sz]e (?:my |the )?(?:preferences|requirements))\??",
        text, re.I,
    ):
        return ControlIntent('preferences')
    if re.fullmatch(r'(?:please )?undo (?:my |the |last )?(?:shortlist|selection)(?: (?:edit|change))?[.!?]*', text, re.I):
        return ControlIntent('undo_selection')
    if re.fullmatch(r'(?:please )?redo (?:my |the |last )?(?:shortlist|selection)(?: (?:edit|change))?[.!?]*', text, re.I):
        return ControlIntent('redo_selection')
    if re.fullmatch(r'(?:please )?(?:undo|reverse) (?:my |the |last )?(?:product )?(?:rejection|rejections|exclusion|exclusions)[.!?]*', text, re.I):
        return ControlIntent('undo_rejection')
    if re.fullmatch(r'(?:please )?redo (?:my |the |last )?(?:product )?(?:rejection|rejections|exclusion|exclusions)[.!?]*', text, re.I):
        return ControlIntent('redo_rejection')
    postpone = re.fullmatch(r"(?:please )?(?:(?:don't|do not) (?:finali[sz]e|confirm)(?: (?:my |the )?(?:selection|shortlist))?(?: yet)?|(?:i am|i'm) not ready to finali[sz]e|不要确认(?:最终)?选择)[.!?。！？]*", text, re.I)
    conditional = re.fullmatch(r'(?:if .+, (?:please )?finali[sz]e (?:my |the )?(?:selection|shortlist)|finali[sz]e (?:my |the )?(?:selection|shortlist) (?:after|when|if|only if) .+)[.!?]*', text, re.I)
    if postpone or conditional:
        return ControlIntent('hold', attribute='conditional' if conditional else None)
    # Negating an edit is not an edit. Keep "don't like #2" on the separate
    # rejection path: it expresses a real negative product preference.
    negated_edit = re.fullmatch(r"(?:please )?(?:don't|do not|never) (?:remove|delete|drop|deselect|clear|empty|select|choose|pick|reject|hide|export|generate|compare)\b[^;,.!?]*[.!?]*", text, re.I)
    if negated_edit:
        return ControlIntent('retain')
    topic = re.fullmatch(r'(?:(?:what|how) about (?:its |the )?|(?:its |the ))?(material|fabric|price|cost|fit)(?:,? please)?[?.!]*', text, re.I)
    if topic:
        attribute = {'fabric': 'material', 'cost': 'price'}.get(topic.group(1).casefold(), topic.group(1).casefold())
        return ControlIntent('detail', (), attribute, uses_focus=True)
    rank_ref = r'(?:it|this one|that one|#\d+|(?:the\s+)?(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)(?:\s+(?:one|item|product))?)'
    comfort_compare = re.fullmatch(
        r'which(?: one| item| product| of these)? (?:is|would be|feels?) (?:the )?(?:most comfortable|comfiest)(?: to wear)?[?.!]*',
        text, re.I)
    comfort_item = re.fullmatch(
        rf'(?:is|would) (?P<ref>{rank_ref}) (?:be )?(?:comfortable|comfy)(?: to wear)?[?.!]*',
        text, re.I)
    if comfort_compare or comfort_item:
        reference = comfort_item.group('ref') if comfort_item else ''
        explicit_rank = re.fullmatch(r'#([1-9]\d*)', reference)
        ranks = (int(explicit_rank.group(1)),) if explicit_rank else _ranks(reference)
        return ControlIntent('comfort_question', ranks,
                             uses_focus=reference.casefold() in {'it', 'this one', 'that one'})
    zh_ref = r'(?:第[一二三四五六七八九十\d]+[款个件]|(?:那)?(?:它|这款|那款))'
    similar = re.fullmatch(
        rf'(?:(?:(?:can you )?(?:show|find|give)(?: me)?\s+)?(?:more|something|some options|options?)\s+'
        rf'(?:like|similar to)\s+(?P<ref>{rank_ref}))[?.!]*', text, re.I)
    if similar:
        reference = similar.group('ref')
        return ControlIntent('similar', _ranks(reference), uses_focus=reference.casefold() in {'it', 'this one', 'that one'})
    if (re.fullmatch(r'which (?:of these |one |item |product )?should i (?:buy|choose|pick|go with)[?.!]*', text, re.I)
            or re.fullmatch(r'what (?:should i|would you) (?:buy|choose|pick|recommend)[?.!]*', text, re.I)):
        return ControlIntent('preference_compare', attribute='buying_advice')
    if (re.fullmatch(r'which (?:of these |one |item )?(?:is|would be) (?:better|best)'
                     r'(?: for me| for my needs)?[?.!]*', text, re.I)
            or re.fullmatch(r'which (?:is|would be) better,?\s+#\d+\s+(?:or|and)\s+#\d+'
                            r'(?: for me)?[?.!]*', text, re.I)
            or re.fullmatch(r'between #\d+\s+(?:and|or)\s+#\d+,?\s+'
                            r'which (?:is|would be) better(?: for me)?[?.!]*', text, re.I)):
        # Preserve out-of-range references so the comparison can reject them explicitly.
        explicit = tuple(dict.fromkeys(int(rank) for rank in re.findall(r'#([1-9]\d*)', text)))
        return ControlIntent('preference_compare', explicit)
    review_question = re.fullmatch(
        r"(?:which|what)(?:\s+(?:one|item|product|of these))?\s+(?:has|is)\s+(?:the\s+)?"
        r"(?P<metric>best reviews|highest rating|highest rated|best rated|most reviews|most ratings)"
        r"(?:\s+(?:one|item|product))?[?.!]*", text, re.I)
    if review_question:
        metric = review_question.group('metric').casefold()
        mode = ('most_reviews' if metric in {'most reviews', 'most ratings'} else
                'best_reviews' if metric == 'best reviews' else 'highest_rated')
        return ControlIntent('review_compare', attribute=mode)
    rank_reason = (re.fullmatch(rf'why (?:is|was) (?P<ref>{rank_ref}) (?:first|ranked (?:first|#?\d+))[?.!]*', text, re.I)
                   or re.fullmatch(rf'why did you (?:pick|choose|recommend|rank) (?P<ref>{rank_ref})(?: first)?[?.!]*', text, re.I)
                   or re.fullmatch(rf'why (?P<ref>{rank_ref})[?.!]*', text, re.I)
                   or re.fullmatch(rf'what makes (?P<ref>{rank_ref}) a good match[?.!]*', text, re.I))
    if rank_reason:
        reference = rank_reason.group('ref')
        return ControlIntent('explain_rank', _ranks(reference),
                             'claimed_first' if re.search(r'\bfirst\b', text, re.I) else None,
                             reference.casefold() in {'it', 'this one', 'that one'})
    cheaper = re.fullmatch(
        rf'(?:(?:do you have|have you got|can you show me|show me|find me)\s+)?'
        rf'(?:(?:anything|something|any|some|options?)\s+)?'
        rf'(?:a bit |much )?(?:cheaper|less expensive|more affordable)'
        rf'(?:\s+(?:options?|ones?))?(?:\s+than\s+(?P<ref>{rank_ref}))?'
        r'(?:,?\s*please)?[?.!]*', text, re.I)
    cheapest = re.fullmatch(
        r'(?:(?:which(?: one| of these)? is|what(?: is|\'s)|show me)\s+(?:the\s+)?'
        r'(?:cheapest|least expensive|most affordable)(?:\s+(?:one|option|item))?'
        r'|(?:which|what)\s+(?:one|option|item)\s+is\s+(?:the\s+)?cheapest)'
        r'(?:,?\s*please)?[?.!]*', text, re.I)
    if cheaper:
        reference = cheaper.group('ref') or ''
        return ControlIntent('price_compare', _ranks(reference), 'cheaper',
                             reference.casefold() in {'it', 'this one', 'that one'})
    if cheapest:
        return ControlIntent('price_compare', (), 'cheapest')
    material_check = re.fullmatch(
        rf'is {rank_ref} (?:made (?:of|from) )?(?:a )?(?:(?P<purity>100\s*%|pure|all)[ -]?)?'
        r'(?P<fiber>cotton|polyester|linen|wool|silk|nylon|leather|spandex|elastane|rayon|viscose|acrylic|modal|cashmere)'
        r'(?:\s+blend)?[?.!]*', text, re.I)
    material = (re.fullmatch(rf'{zh_ref}(?:的)?(?:是什么材质|什么材质|材质是什么|是什么成分|成分是什么)[?？。.!]*', text)
                or re.fullmatch(rf'what (?:material|fabric) is {rank_ref}(?: made (?:of|from))?[?.!]*', text, re.I))
    price = (re.fullmatch(rf'{zh_ref}(?:的)?(?:多少钱|价格多少|价格是多少)[?？。.!]*', text)
             or re.fullmatch(rf'how much (?:is|does) {rank_ref}(?: cost)?[?.!]*', text, re.I))
    fit = (re.fullmatch(rf'{zh_ref}(?:的)?(?:宽松吗|修身吗|是宽松的吗|是修身的吗|是什么版型|什么版型|版型怎么样)[?？。.!]*', text)
           or re.fullmatch(rf'is {rank_ref} (?:loose(?:[ -]fit)?|relaxed(?:[ -]fit)?|slim[ -]fit|fitted|oversized)[?.!]*', text, re.I)
           or re.fullmatch(rf'what (?:is the fit of|fit is) {rank_ref}[?.!]*', text, re.I))
    if material or material_check or price or fit:
        focused = bool(re.search(r'\b(?:it|this one|that one)\b|它|这款|那款', text, re.I))
        if material_check:
            fiber = material_check.group('fiber').casefold()
            target = f'pure {fiber}' if material_check.group('purity') else fiber
            attribute = f'material_check:{target}'
        else:
            attribute = 'material' if material else 'price' if price else 'fit'
        return ControlIntent('detail', _ranks(text), attribute, focused)
    # A question about a displayed item is not consent to change preferences.
    # Bind ranks from the reference only: a size or price in the question is
    # not another product rank. Explicit multi-sentence requests stay separate.
    # A polite, explicit replacement request still belongs to requirement
    # parsing. Do not infer a change from availability/suitability questions.
    if re.fullmatch(r'(?:can|could) it be [^?!;,.]+ instead[?!.]*', text, re.I):
        return None
    question = re.fullmatch(rf'(?:is|does|can|could|will|would|has) (?P<ref>{rank_ref})\s+[^?!;,.]+[?!.]*', text, re.I)
    if question:
        reference = question.group('ref')
        focused = reference.casefold() in {'it', 'this one', 'that one'}
        return ControlIntent('detail', _ranks(reference), 'unsupported', focused)
    for action in ("clear", "finalize", "handoff", "reject", "remove", "compare", "select"):
        if CONTROL_PATTERNS[action].search(text):
            ranks = _ranks(text)
            if action in {'reject', 'remove', 'select'} and not ranks and re.search(
                r'\$\s*\d|\b(?:budget|price|dollars?|usd|spending limit)\b', text, re.I
            ):
                return None  # A price is not a displayed product rank.
            return ControlIntent(action, ranks)
    return None
