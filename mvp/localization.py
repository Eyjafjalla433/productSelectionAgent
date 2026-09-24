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
        return "这次搜索未能完成，请重新描述你的需求。"
    if ask_attribute:
        if reason == "contradictory_budget":
            return "你给出的最低价格高于最高价格，请重新告诉我预算范围。"
        if reason == "empty_eligible_pool":
            return "当前硬条件下没有可验证的商品。你愿意调整哪一项条件？"
        questions = {
            "category": "你想找哪一类商品？",
            "budget": "你的预算范围是多少？目录价格以美元计。",
            "feature": "你最看重哪一个具体功能或特点？",
            "other": "请再告诉我其他重要要求，例如材质、颜色、用途或预算。",
        }
        return questions.get(ask_attribute, "请再提供一项能帮助筛选商品的要求。")
    if product_count:
        return f"我找到了 {product_count} 件通过当前目录条件检查的商品。下面列出了匹配理由和需要注意的地方。"
    return "当前条件下没有商品通过目录检查。你可以修改一项硬条件后再试。"


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
        return "已清空候选清单。"
    if action == "remove":
        return f"已更新候选清单，目前保留 {selection_count} 件商品。"
    if action == "reject":
        return "已记录商品级否决反馈；这些商品不会再次出现在后续结果中。"
    if action == "select":
        return f"已更新候选清单，目前选中 {selection_count} 件商品。"
    if action == "compare":
        return (
            f"已选中 {selection_count} 件商品，结构化对比已经准备好。"
            if handoff_ready else
            "请先从最近展示的结果中选择至少一件商品，再进行对比。"
        )
    if action == "finalize":
        return (
            "最终选型已经确认，并生成了带时间和轮次的结构化交接。"
            if handoff_ready else
            "请先选择至少一件已经展示的商品，再确认最终选型。"
        )
    return (
        "结构化选型交接草稿已经准备好。"
        if handoff_ready else
        "请先选择至少一件已经展示的商品，再完成选型。"
    )
