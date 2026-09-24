"""Small presentation-layer localization for the runnable web MVP."""

from __future__ import annotations

import re
from typing import Any


CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def message_locale(message: str, current: str = "en") -> str:
    return "zh" if CJK_RE.search(message) else current


def localized_agent_message(
    *,
    locale: str,
    fallback: str,
    ask_attribute: str | None,
    receipt: dict[str, Any],
    product_count: int,
) -> str:
    if locale != "zh":
        return fallback
    reason = str(receipt.get("post_reason") or receipt.get("pre_reason") or "")
    if any(row.get("stage") == "error" for row in receipt.get("timings", ())):
        return "这次搜索出了点问题，还没拿到可靠的结果。你的需求我保留着，我们可以再试一次。"
    if ask_attribute:
        if (receipt.get('question') or {}).get('message_zh'):
            return receipt['question']['message_zh']
        if reason == "contradictory_budget":
            return "我想确认一下预算：最低价比最高价还高了，你希望控制在哪个区间呢？"
        if reason == "empty_eligible_pool":
            return "按你现在的要求，我还没找到能确认合适的款式。也可能是商品资料不全。哪些条件一定要保留，哪些可以稍微灵活一点呢？"
        questions = {
            "category": "可以呀。你现在想挑什么呢，比如连衣裙、鞋子，还是日常用的包？",
            "budget": "你希望大概花多少钱呢？这里的商品价格用美元表示。",
            "feature": "我再帮你缩小一点范围：你更在意哪一点？比如舒服好穿，还是款式和搭配。",
            "other": "还有什么是你特别在意的吗？比如材质、颜色，或者准备在什么场合用。",
        }
        return questions.get(ask_attribute, "你最在意的那一点是什么呢？告诉我后，我再帮你挑得更细一些。")
    if product_count:
        return f"我按你的要求挑出了 {product_count} 款，咱们不用一下看完。先看看排在前面的几款，各自的特点和需要留意的地方我都放在下面了。"
    return "这轮还没有找到能确认符合你要求的商品。我先保留你的条件；如果你愿意，我们可以从最想保留的一点开始重新挑。"


def localized_control_message(
    *,
    locale: str,
    action: str,
    selection_count: int,
    handoff_ready: bool,
    fallback: str,
) -> str:
    if locale != "zh":
        return fallback
    if action == "clear":
        return "好，先把这些放下，我们重新挑。"
    if action == "remove":
        return f"好，已经帮你移走了，目前还留着 {selection_count} 款。"
    if action == "reject":
        return "明白，这几款不合你的心意，我先帮你排除。接下来继续挑同类商品时，就不再推荐它们了。"
    if action == "select":
        return f"好，先帮你留着这 {selection_count} 款，不着急决定，还可以再比较一下。"
    if action == "compare":
        return (
            f"好呀，我们把这 {selection_count} 款放在一起慢慢看。我会把能确认的特点和还需要核实的地方分开说，帮你看看哪一款更贴近你的需求。"
            if handoff_ready else
            "你想比较哪几款呢？可以告诉我编号，比如“第一款和第三款”，我再帮你一起看。"
        )
    if action == "finalize":
        return (
            "好，那就先定下你选的这几款，我帮你把清单整理好了。这里只是保存选择，还没有下单；购买前记得再看一下尺码、当前价格和库存。"
            if handoff_ready else
            "你最想留下哪一款呢？告诉我编号，我就帮你整理好选购清单。"
        )
    return (
        "我把你目前选中的款式整理好了，可以先保存下来，再慢慢考虑。"
        if handoff_ready else
        "先告诉我想留下哪几款吧，我来帮你整理。"
    )
