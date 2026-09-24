"""Reproducible, zero-token end-to-end smoke test for the local web runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .audit import verify_audit
from .server import AgentRuntime, find_catalog


DEFAULT_QUERY = "我想要一条蓝色棉质连衣裙，价格不超过50美元。"


def _usage(result: dict[str, Any]) -> int:
    usage = result.get("receipt", {}).get("model_usage", {})
    return int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)


def run_smoke(catalog: Path) -> dict[str, Any]:
    runtime = AgentRuntime.create(catalog, orchestration_mode="adaptive")
    session = runtime.new_session()
    session_id = session["session_id"]

    search = runtime.chat(session_id, DEFAULT_QUERY)
    products = search.get("products") or []
    if len(products) < 2:
        raise RuntimeError(f"expected at least two products, received {len(products)}")
    if len(products) > 10:
        raise RuntimeError(f"Top 10 boundary violated: received {len(products)} products")

    chosen = [product["parent_asin"] for product in products[:2]]
    for parent_asin in chosen:
        runtime.update_selection(session_id, parent_asin=parent_asin, selected=True)

    compared = runtime.chat(session_id, "比较第一个和第二个")
    finalized = runtime.chat(session_id, "确认最终选择")
    finalized_handoff = runtime.selection_handoff(session_id)

    if finalized_handoff.get("status") != "finalized":
        raise RuntimeError(f"expected finalized handoff, received {finalized_handoff.get('status')!r}")
    if finalized_handoff.get("decision", {}).get("selected_asins") != chosen:
        raise RuntimeError("finalized ASIN order did not match the selected products")

    changed = runtime.chat(session_id, "改成红色")
    reopened_handoff = runtime.selection_handoff(session_id)
    if not changed.get("receipt", {}).get("selection_finalization_invalidated"):
        raise RuntimeError("requirement change did not invalidate the finalized selection")
    if reopened_handoff.get("status") != "ready_for_comparison":
        raise RuntimeError("requirement change did not reopen the shortlist for comparison")
    if reopened_handoff.get("decision", {}).get("selected_asins") != chosen:
        raise RuntimeError("reopening finalization unexpectedly changed the shortlist")

    audit = runtime.audit(session_id)
    audit_errors = verify_audit(audit)
    if audit_errors:
        raise RuntimeError("audit verification failed: " + "; ".join(audit_errors))

    fully_supported = sum(
        1
        for product in products
        if product.get("match", {}).get("hard_supported")
        == product.get("match", {}).get("hard_total")
    )
    token_count = sum(_usage(result) for result in (search, compared, finalized, changed))
    if token_count:
        raise RuntimeError(f"offline smoke unexpectedly consumed {token_count} model tokens")

    return {
        "ok": True,
        "catalog_products": runtime.agent.catalog_size,
        "query": DEFAULT_QUERY,
        "shown_products": len(products),
        "fully_supported_products": fully_supported,
        "selected_asins": chosen,
        "comparison_status": compared.get("handoff", {}).get("status"),
        "final_status": finalized.get("selection_state", {}).get("status"),
        "handoff_status": finalized_handoff.get("status"),
        "post_requirement_change_status": reopened_handoff.get("status"),
        "finalization_invalidated": changed.get("receipt", {}).get("selection_finalization_invalidated"),
        "audit_turns": audit.get("turn_count"),
        "audit_head_sha256": audit.get("integrity", {}).get("head"),
        "model_tokens": token_count,
        "cloud_model": session.get("cloud_model"),
        "data_boundary": session.get("data_boundary"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", help="optional catalog.jsonl path")
    args = parser.parse_args()
    result = run_smoke(find_catalog(args.catalog))
    # ASCII-safe JSON remains readable in legacy Windows shells and CI logs.
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
