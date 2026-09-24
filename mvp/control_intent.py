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
    "finalize": re.compile(r"(?:finali[sz]e|finish|confirm).{0,12}(?:shortlist|selection|choice)?|(?:完成|确认|确定).{0,8}(?:选择|选型|候选)", re.I),
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


def _ranks(message: str) -> tuple[int, ...]:
    lowered = message.casefold()
    found: list[int] = []
    for word, rank in ORDINALS.items():
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            found.append(rank)
    for raw in re.findall(r"(?<![$\d])#?([1-9]|10)(?!\d)", lowered):
        found.append(int(raw))
    return tuple(dict.fromkeys(found))


def parse_control_intent(message: str) -> ControlIntent | None:
    text = " ".join(message.strip().split())
    if not text:
        return None
    for action in ("clear", "finalize", "handoff", "reject", "remove", "compare", "select"):
        if CONTROL_PATTERNS[action].search(text):
            return ControlIntent(action, _ranks(text))
    return None
