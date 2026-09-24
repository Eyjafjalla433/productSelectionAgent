"""Read-only integration probes using a temporary, artificial catalog.

These observations are not public-200 evaluation scores or acceptance tests.
Run with Python >=3.10 from any directory; no model downloads are needed.
"""

import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import ranking_pipeline  # Sets up the existing sibling-module import paths.
from intent_router import IntentRouter
from state_memory import StateMemoryManager
from ranking_pipeline.agent import RankingAgent
from ranking_pipeline.memory_context import snapshot_to_requirements
from techjam_agent.contracts import Requirements
from techjam_agent.retrieval import LiteTop50CandidateGenerator


def main():
    observations = {}
    message = "I'm looking for Earrings Hoop, but I'm still exploring."
    intent = IntentRouter().understand(message)
    snapshot = StateMemoryManager().update("s", "u", message)
    observations["same_message_intent"] = {
        "message": message,
        "router_intent": intent.intent_type,
        "router_route": getattr(intent, "route", None),
        "router_owns_route_selection": hasattr(intent, "route"),
        "state_intent": snapshot.intent.value,
        "state_route": snapshot.route.value,
    }
    snapshot = StateMemoryManager().update("s", "u", "I need shoes, not leather")
    observations["material_negation"] = {
        "message": "I need shoes, not leather",
        "must_match": snapshot.must_match,
        "must_not_match": snapshot.must_not_match,
    }
    manager = StateMemoryManager()
    for message in ["show me a black dress", "blue instead", "black instead"]:
        snapshot = manager.update("s", "u", message)
    observations["return_to_previously_rejected_color"] = {
        "messages": ["show me a black dress", "blue instead", "black instead"],
        "must_match": snapshot.must_match,
        "must_not_match": snapshot.must_not_match,
    }
    snapshot = StateMemoryManager().update("s", "u", "a blue dress under $50, not black")
    observations["snapshot_adapter"] = {
        "input_must_match": snapshot.must_match,
        "input_must_not_match": snapshot.must_not_match,
        "output_requirements": asdict(snapshot_to_requirements(snapshot)),
    }
    with tempfile.TemporaryDirectory() as directory:
        catalog = Path(directory) / "catalog.jsonl"
        products = [
            {"parent_asin": f"P{i:03}", "title": "Cotton blue shirt",
             "categories": ["Clothing", "Shirts"], "features": ["cotton"],
             "details": {"Color": "blue"}, "description": [],
             "store": "Example", "price": 500, "rating_number": 1}
            for i in range(60)
        ]
        catalog.write_text("".join(json.dumps(p) + "\n" for p in products))
        candidates = LiteTop50CandidateGenerator(catalog).generate(
            Requirements("Shirts", ("under $50",), ()), session_id="s", turn=3
        )
        observations["numeric_budget_filter"] = {
            "all_catalog_prices": 500, "requested_maximum": 50,
            "returned_candidates": len(candidates.candidates),
            "candidate_snapshot_has_price": "price" in candidates.candidates[0].product,
        }
        agent = RankingAgent(catalog, retrieval_mode="lite", reranker_mode="locked")
        agent.reset("s", {})
        response = agent.respond("s", "I'm looking for Shirts. A key requirement is: cotton.", 11, 10)
        observations["direct_agent_turn_11"] = {
            "returned_recommendations": len(response["recommendations"]),
            "note": "The evaluator limits turns; the agent itself accepts turn 11.",
        }
    print(json.dumps(observations, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
