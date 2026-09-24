"""Evaluate the four product layers without exposing labels to the runtime.

A uses the unchanged organizer evaluator.  B/C/D are exercised through the
product-facing ``AgentRuntime`` with simulator-generated user messages.  The
ground-truth ASIN is used by the evaluator/simulator only and is never passed to
the Agent or included in a product-facing request.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import gc
import json
from pathlib import Path
import sys
from time import perf_counter
from typing import Any

from mvp.audit import verify_audit
from mvp.server import (
    AgentRuntime,
    CHAT_SCHEMA_VERSION,
    PRODUCT_DETAIL_SCHEMA_VERSION,
    SELECTION_SCHEMA_VERSION,
)

from .common import KIT, ROOT, check_kit, source_hashes, write_json
from .evaluate import TimedAgent, official_evaluator


REPORT_SCHEMA_VERSION = "show-me-your-agent.abcd-evaluation.v1"


def _selected_samples(samples: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Choose a deterministic, scenario-balanced public subset."""
    if limit <= 0 or limit >= len(samples):
        return list(samples)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        groups[str(sample.get("scenario_type") or "unknown")].append(sample)
    selected: list[dict[str, Any]] = []
    names = sorted(groups)
    while len(selected) < limit and any(groups.values()):
        for name in names:
            if groups[name] and len(selected) < limit:
                selected.append(groups[name].pop(0))
    return selected


def _usage(result: dict[str, Any]) -> int:
    usage = result.get("assistant", {}).get("usage") or {}
    return max(0, int(usage.get("prompt_tokens") or 0)) + max(
        0, int(usage.get("completion_tokens") or 0)
    )


def _validate_product(product: object, errors: list[str], label: str) -> None:
    if not isinstance(product, dict):
        errors.append(f"{label}: product is not an object")
        return
    for field in ("rank", "parent_asin", "title", "match", "advice"):
        if field not in product:
            errors.append(f"{label}: product missing {field}")
    advice = product.get("advice") or {}
    for kind in ("pros", "cons"):
        points = advice.get(kind) or []
        if not points:
            errors.append(f"{label}: product has no {kind}")
        for point in points:
            if not point.get("source") or not point.get("evidence"):
                errors.append(f"{label}: {kind} point is not source-labelled")


def _run_product_session(
    runtime: AgentRuntime,
    evaluator: Any,
    sample: dict[str, Any],
    categories: dict[str, list[str]],
    products: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Run one public scenario while keeping target data outside runtime calls."""
    session_id = runtime.new_session(deepcopy(sample["user_profile"]))["session_id"]
    target = str(sample["ground_truth"]["parent_asin"])
    card, behavior = evaluator.materialize_hidden_fields(sample, products)
    effective = {**sample, "intent_card": card, "behavior": behavior}
    disclosed: set[str] = set()
    boundary_used = False
    override_applied = sample["scenario_type"] != "intent_override"
    message = evaluator.initial_message(
        effective, evaluator.coarse_category(categories.get(target, [])), disclosed
    )
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    latest_products: list[dict[str, Any]] = []

    # Reserve two turns for compare/finalize so C and D are exercised too.
    for next_turn in range(1, 9):
        result = runtime.chat(session_id, message)
        results.append(result)
        if result.get("schema_version") != CHAT_SCHEMA_VERSION:
            errors.append(f"turn {next_turn}: wrong chat schema")
        if result.get("turn") != next_turn:
            errors.append(f"turn {next_turn}: non-sequential response")
        rows = result.get("products") or []
        ids = [row.get("parent_asin") for row in rows if isinstance(row, dict)]
        if len(rows) > 10 or len(ids) != len(set(ids)):
            errors.append(f"turn {next_turn}: invalid recommendation cardinality")
        for product in rows:
            _validate_product(product, errors, f"turn {next_turn}")
        if rows:
            latest_products = rows
            # An override session is useful only after the changed intent reached C.
            if override_applied:
                break
        override = effective.get("behavior", {}).get("override") or {}
        if not override_applied and next_turn + 1 == int(override.get("turn", 3)):
            override_applied = True
            changed = str(override.get("new_value", ""))
            if changed:
                disclosed.add(changed)
            message = str(override.get("message") or "Actually, please use my new requirement.")
        else:
            message, boundary_used = evaluator.customer_reply(
                effective,
                result.get("assistant", {}).get("ask_attribute"),
                disclosed,
                boundary_used,
            )

    detail: dict[str, Any] | None = None
    handoff: dict[str, Any] | None = None
    if latest_products:
        detail = runtime.product_detail(session_id, latest_products[0]["parent_asin"])
        if detail.get("schema_version") != PRODUCT_DETAIL_SCHEMA_VERSION:
            errors.append("product detail has wrong schema")
        ranks = " and ".join(f"#{row['rank']}" for row in latest_products[:2])
        compared = runtime.chat(session_id, f"Compare {ranks}")
        results.append(compared)
        finalized = runtime.chat(session_id, "Finalize my selection")
        results.append(finalized)
        handoff = runtime.selection_handoff(session_id)
        if handoff.get("schema_version") != SELECTION_SCHEMA_VERSION:
            errors.append("handoff has wrong schema")
        if handoff.get("status") != "finalized":
            errors.append("selection did not finalize")
        selected = handoff.get("selected_products") or []
        comparison_rows = handoff.get("comparison_summary", {}).get("rows") or []
        if len(comparison_rows) != len(selected):
            errors.append("comparison rows do not match selected products")
    else:
        errors.append("no products reached the presentation layer")

    audit = runtime.audit(session_id)
    errors.extend(f"audit: {message}" for message in verify_audit(audit))
    total_tokens = sum(_usage(result) for result in results)
    if total_tokens:
        errors.append(f"offline path reported {total_tokens} model tokens")

    evidence = Counter()
    hard_coverages: list[float] = []
    for result in results:
        for product in result.get("products") or []:
            match = product.get("match") or {}
            hard_coverages.append(float(match.get("hard_coverage", 0.0)))
            for kind in ("pros", "cons"):
                for point in (product.get("advice") or {}).get(kind) or []:
                    evidence["points"] += 1
                    if point.get("source") and point.get("evidence"):
                        evidence["source_labelled"] += 1
            for signal in match.get("signals") or []:
                evidence[f"signal_{signal.get('status', 'unknown')}"] += 1

    get_session_traces = getattr(runtime.agent, "get_session_traces", None)
    trace_rows = (
        get_session_traces(session_id)
        if callable(get_session_traces)
        else [row for row in runtime.agent.trace if row.get("session_id") == session_id]
    )
    stages = Counter(
        event.get("stage")
        for row in trace_rows
        for event in row.get("events", [])
        if event.get("stage")
    )
    if not stages["1_intent"]:
        errors.append("C trace has no intent stage")
    if latest_products and not stages["4B_ranking"]:
        errors.append("C trace has no ranking stage")

    return {
        "sample_id": sample["sample_id"],
        "scenario_type": sample["scenario_type"],
        "turn_count": len(results),
        "product_count": len(latest_products),
        "finalized": bool(handoff and handoff.get("status") == "finalized"),
        "detail_fields": sorted(
            key
            for key in (detail or {})
            if key not in {"schema_version", "session_id", "source_note"}
        ),
        "average_hard_coverage": (
            round(sum(hard_coverages) / len(hard_coverages), 6) if hard_coverages else None
        ),
        "evidence": dict(evidence),
        "trace_stages": dict(stages),
        "model_tokens": total_tokens,
        "errors": errors,
    }


def run_product_acceptance(
    catalog: Path,
    samples: list[dict[str, Any]],
    evaluator: Any,
    categories: dict[str, list[str]],
    products: dict[str, dict[str, Any]],
    *,
    session_limit: int = 12,
) -> dict[str, Any]:
    chosen = _selected_samples(samples, session_limit)
    runtime = AgentRuntime.create(
        catalog,
        orchestration_mode="adaptive",
        max_sessions=max(16, len(chosen) + 1),
        session_ttl_seconds=3600,
    )
    sessions = [
        _run_product_session(runtime, evaluator, sample, categories, products)
        for sample in chosen
    ]
    errors = [
        {"sample_id": row["sample_id"], "message": message}
        for row in sessions
        for message in row["errors"]
    ]
    product_sessions = [row for row in sessions if row["product_count"]]
    evidence_points = sum(row["evidence"].get("points", 0) for row in sessions)
    source_labelled = sum(row["evidence"].get("source_labelled", 0) for row in sessions)
    coverage = [
        row["average_hard_coverage"]
        for row in sessions
        if row["average_hard_coverage"] is not None
    ]
    return {
        "configuration": {
            "session_limit": session_limit,
            "selected_sessions": len(chosen),
            "orchestration_mode": "adaptive",
            "model_provider": "off",
            "target_data_passed_to_runtime": False,
        },
        "B_evidence": {
            "product_sessions": len(product_sessions),
            "evidence_points": evidence_points,
            "source_labelled_rate": round(source_labelled / evidence_points, 6) if evidence_points else 0.0,
            "average_hard_coverage": round(sum(coverage) / len(coverage), 6) if coverage else None,
        },
        "C_orchestration": {
            "audited_sessions": len(sessions),
            "zero_token_sessions": sum(row["model_tokens"] == 0 for row in sessions),
            "contract_error_count": len(errors),
        },
        "D_experience": {
            "sessions_with_products": len(product_sessions),
            "finalized_sessions": sum(row["finalized"] for row in sessions),
            "detail_sessions": sum(bool(row["detail_fields"]) for row in sessions),
        },
        "errors": errors,
        "sessions": sessions,
    }


def run_official_evaluation(catalog: Path, samples: list[dict[str, Any]], evaluator: Any) -> dict[str, Any]:
    from agent import Agent

    started = perf_counter()
    agent = Agent(catalog, trace_enabled=False)
    measured = TimedAgent(agent)
    ids, categories, products = evaluator.catalog_index(catalog)
    evaluation = evaluator.evaluate(measured, samples, ids, categories, products)
    return {
        "evaluation": evaluation,
        "respond_errors": measured.boundary_errors,
        "agent_errors": agent.errors,
        "elapsed_seconds": round(perf_counter() - started, 3),
        "catalog_count": len(ids),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=KIT / "data/catalog.jsonl")
    parser.add_argument("--dataset", type=Path, default=KIT / "data/public_set.jsonl")
    parser.add_argument("--product-sessions", type=int, default=12)
    parser.add_argument("--skip-official", action="store_true", help="run only B/C/D product acceptance")
    parser.add_argument("--output", type=Path, default=ROOT / "results/abcd-public.json")
    args = parser.parse_args(argv)
    if args.product_sessions < 1:
        parser.error("--product-sessions must be positive")
    if args.catalog.resolve() == (KIT / "data/catalog.jsonl").resolve() and args.dataset.resolve() == (KIT / "data/public_set.jsonl").resolve():
        check_kit()
    evaluator = official_evaluator()
    samples = evaluator.load_jsonl(args.dataset)

    official: dict[str, Any] | None = None
    if not args.skip_official:
        official = run_official_evaluation(args.catalog, samples, evaluator)
        # Release A's indexes before constructing the product runtime.
        gc.collect()
    ids, categories, products = evaluator.catalog_index(args.catalog)
    product = run_product_acceptance(
        args.catalog,
        samples,
        evaluator,
        categories,
        products,
        session_limit=args.product_sessions,
    )
    gates = {
        "A_official_completed": bool(
            official
            and official["evaluation"].get("sample_count") == len(samples)
            and not official["respond_errors"]
            and not official["agent_errors"]
        ) if not args.skip_official else None,
        "B_all_points_source_labelled": product["B_evidence"]["source_labelled_rate"] == 1.0,
        "C_contracts_and_audits_valid": product["C_orchestration"]["contract_error_count"] == 0,
        "C_default_zero_tokens": product["C_orchestration"]["zero_token_sessions"] == len(product["sessions"]),
        "D_detail_compare_finalize_complete": (
            product["D_experience"]["finalized_sessions"] == len(product["sessions"])
            and product["D_experience"]["detail_sessions"] == len(product["sessions"])
        ),
    }
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "methodology": {
            "dataset": str(args.dataset),
            "catalog": str(args.catalog),
            "labels_boundary": "Ground truth is evaluator-only and is never passed to AgentRuntime.",
            "A": "unchanged organizer evaluator over the public set",
            "B": "catalog-grounded match/advice evidence on product-facing responses",
            "C": "multi-turn state, traces, controls, zero-token usage and audit-chain contracts",
            "D": "cards, lazy detail, comparison handoff and explicit finalization",
        },
        "A_official": official,
        "product_acceptance": product,
        "gates": gates,
        "passed": all(value is not False for value in gates.values()),
        "source_sha256": source_hashes(),
    }
    write_json(args.output, report)
    summary = {
        "passed": report["passed"],
        "gates": gates,
        "A": None if official is None else {
            key: official["evaluation"][key]
            for key in ("sample_count", "hit_rate_at_10", "mrr", "mttc", "recommended_technical_score")
        },
        "B": product["B_evidence"],
        "C": product["C_orchestration"],
        "D": product["D_experience"],
        "report": str(args.output),
    }
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
