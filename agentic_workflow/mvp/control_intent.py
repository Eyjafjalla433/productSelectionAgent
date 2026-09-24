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


def split_detail_and_requirements(message: str):
    """Only split an explicit detail-first question plus a separate request.

    Keep the entire trailing request intact so multi-slot extraction and undo
    still operate on one user turn. Selection/purchase controls are not implied.
    """
    parts = re.split(r'[?？。.!，,;；]\s*(?:另外|顺便|同时|also\b|and\s+also\b)\s*[,，]?\s*',
                     message.strip(), maxsplit=1, flags=re.I)
    if len(parts) != 2 or not parts[1].strip():
        return None
    detail = parse_control_intent(parts[0])
    if detail is None or detail.action != 'detail' or parse_control_intent(parts[1]) is not None:
        return None
    return detail, parts[1].strip()


def parse_control_intent(message: str) -> ControlIntent | None:
    text = " ".join(message.strip().split())
    if not text:
        return None
    if re.fullmatch(r'(?:please )?undo (?:my |the |last )?(?:shortlist|selection)(?: (?:edit|change))?[.!?]*', text, re.I):
        return ControlIntent('undo_selection')
    if re.fullmatch(r'(?:please )?redo (?:my |the |last )?(?:shortlist|selection)(?: (?:edit|change))?[.!?]*', text, re.I):
        return ControlIntent('redo_selection')
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
    zh_ref = r'(?:第[一二三四五六七八九十\d]+[款个件]|(?:那)?(?:它|这款|那款))'
    material = (re.fullmatch(rf'{zh_ref}(?:的)?(?:是什么材质|什么材质|材质是什么|是什么成分|成分是什么)[?？。.!]*', text)
                or re.fullmatch(rf'what (?:material|fabric) is {rank_ref}(?: made (?:of|from))?[?.!]*', text, re.I)
                or re.fullmatch(rf'is {rank_ref} (?:made (?:of|from) )?(?:a )?(?:(?:100\s*%|pure|all)[ -]?)?(?:cotton|polyester|linen|wool|silk|nylon|leather|spandex|elastane|rayon|viscose|acrylic|modal|cashmere)(?:\s+blend)?[?.!]*', text, re.I))
    price = (re.fullmatch(rf'{zh_ref}(?:的)?(?:多少钱|价格多少|价格是多少)[?？。.!]*', text)
             or re.fullmatch(rf'how much (?:is|does) {rank_ref}(?: cost)?[?.!]*', text, re.I))
    fit = (re.fullmatch(rf'{zh_ref}(?:的)?(?:宽松吗|修身吗|是宽松的吗|是修身的吗|是什么版型|什么版型|版型怎么样)[?？。.!]*', text)
           or re.fullmatch(rf'is {rank_ref} (?:loose(?:[ -]fit)?|relaxed(?:[ -]fit)?|slim[ -]fit|fitted|oversized)[?.!]*', text, re.I)
           or re.fullmatch(rf'what (?:is the fit of|fit is) {rank_ref}[?.!]*', text, re.I))
    if material or price or fit:
        focused = bool(re.search(r'\b(?:it|this one|that one)\b|它|这款|那款', text, re.I))
        return ControlIntent('detail', _ranks(text), 'material' if material else 'price' if price else 'fit', focused)
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
            return ControlIntent(action, _ranks(text))
    return None
