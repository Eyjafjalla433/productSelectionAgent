"""Run the unmodified official evaluator against the submitted Agent."""
import argparse
import importlib.util
import math
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter

from .common import KIT, ROOT, OFFICIAL_HASHES, check_kit, source_hashes, write_json


def official_evaluator():
    check_kit()
    # This loads the exact downloaded evaluator. No patches, rewritten labels,
    # simulator changes, or alternate metric calculations are applied.
    sys.path.insert(0, str(KIT))
    spec = importlib.util.spec_from_file_location("shopping_official_evaluator", KIT / "evaluator/local_evaluator.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def quantile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values) * fraction) - 1)] if values else 0.0


class TimedAgent:
    def __init__(self, agent):
        self.agent = agent
        self.turn_ms = []
        self.boundary_errors = []

    def reset(self, session_id, user_profile):
        return self.agent.reset(session_id, user_profile)

    def respond(self, session_id, user_message, turn, top_k):
        started = perf_counter()
        try:
            response = self.agent.respond(session_id, user_message, turn, top_k)
            if not isinstance(response.get("message"), str) or response.get("ask_attribute") not in {None, "category", "material", "color", "size", "style", "brand", "budget", "feature", "use_case", "other"}:
                raise ValueError("Invalid official response fields")
            rows = response["recommendations"]
            ids = [row["parent_asin"] for row in rows]
            if len(ids) > top_k or len(set(ids)) != len(ids) or any(asin not in self.agent.retriever.products for asin in ids):
                raise ValueError("Invalid or duplicate recommendation IDs")
            return response
        except Exception as exc:
            self.boundary_errors.append({"turn": turn, "type": type(exc).__name__})
            raise
        finally:
            self.turn_ms.append((perf_counter() - started) * 1000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-id", help="Optional one-session demo/diagnostic; omit for all 200")
    parser.add_argument("--ranking-mode", choices=("hybrid", "locked"), default=None)
    parser.add_argument("--orchestration-mode", choices=("adaptive", "score_compat"), default="score_compat")
    parser.add_argument("--clarification-mode", choices=("strict_dynamic", "state_evidence", "fixed_two_dynamic", "one_then_value"))
    parser.add_argument("--trace", action="store_true", help="Save full boundaries; not recommended for all 200 sessions")
    parser.add_argument("--offline-check", action="store_true", help="Block socket connects/DNS for this process during evaluation")
    parser.add_argument("--output", type=Path, default=ROOT / "results/public200.json")
    args = parser.parse_args()
    code_hashes = source_hashes()
    evaluator = official_evaluator()
    if args.offline_check:
        def forbid_network(event, arguments):
            if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
                raise RuntimeError("Network activity rejected by offline evaluation check")
        sys.addaudithook(forbid_network)
    samples = evaluator.load_jsonl(KIT / "data/public_set.jsonl")
    if args.sample_id:
        samples = [sample for sample in samples if sample["sample_id"] == args.sample_id]
        if not samples:
            parser.error("Unknown public sample ID")
    from agent import Agent
    started = perf_counter()
    # The explicit frozen path prevents an environment variable from silently
    # replacing the catalog during the official public reproduction run.
    agent = Agent(KIT / "data/catalog.jsonl", ranking_mode=args.ranking_mode,
                  orchestration_mode=args.orchestration_mode, clarification_mode=args.clarification_mode,
                  trace_enabled=args.trace)
    initialization_seconds = perf_counter() - started
    measured = TimedAgent(agent)
    ids, categories, products = evaluator.catalog_index(KIT / "data/catalog.jsonl")
    started = perf_counter()
    evaluation = evaluator.evaluate(measured, samples, ids, categories, products)
    elapsed = perf_counter() - started
    try:
        import resource
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mb = raw / (1024 * 1024 if sys.platform == "darwin" else 1024)
    except ImportError:
        peak_mb = None
    scorer = agent.reranker.scorer
    report = {
        "configuration": {"entry": "agent.Agent", "retrieval": agent.retriever.mode, "ranking": agent.reranker.mode,
            "orchestration_mode": args.orchestration_mode,
            "clarification_mode": agent.clarification_mode,
            "dense_enabled": False, "llm_enabled": False, "runtime_network_required": False,
            "offline_socket_guard_enabled": args.offline_check,
            "profile_weight": getattr(scorer, "profile_weight", None),
            "hard_constraint_penalty": getattr(scorer, "hard_constraint_penalty", None),
            "pointwise_weight_unused_without_model": getattr(scorer, "pointwise_weight", None)},
        "evaluation": evaluation,
        "runtime": {"python": platform.python_version(), "platform": platform.platform(), "processor": platform.machine(),
            "sqlite": __import__("sqlite3").sqlite_version,
            "initialization_seconds": round(initialization_seconds, 3), "evaluation_seconds": round(elapsed, 3),
            "respond_calls": len(measured.turn_ms),
            "respond_ms_mean": round(statistics.mean(measured.turn_ms), 3),
            "respond_ms_p50": round(quantile(measured.turn_ms, 0.5), 3),
            "respond_ms_p95": round(quantile(measured.turn_ms, 0.95), 3),
            "respond_ms_max": round(max(measured.turn_ms), 3),
            "peak_process_rss_mb_including_evaluator": round(peak_mb, 1) if peak_mb is not None else None,
            "model_api_cost_usd": 0, "cost_note": "No model/API calls. Hardware/electricity costs are not estimated."},
        "source_sha256": code_hashes, "official_artifact_sha256": OFFICIAL_HASHES,
        "errors": agent.errors + measured.boundary_errors,
    }
    if args.trace:
        report["trace"] = agent.trace
    write_json(args.output, report)
    print(__import__("json").dumps({k: v for k, v in evaluation.items() if k != "sessions"}, indent=2))
    print(f"Report: {args.output}\nErrors: {len(report['errors'])}")
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
