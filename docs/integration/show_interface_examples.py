"""Print deterministic schema examples; no catalog writes or model calls.

Explicit operations, state and products below are fixtures, not evidence that
the old modules now execute the new handoff chain.
"""

from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for module in ("intent-recognition", "conversation-state-memory/src", "retrieval-and-reranking"):
    sys.path.insert(0, str(ROOT / module))

from intent_router import IntentRouter, SlotUpdate
from state_memory import StateSnapshotV2
from state_memory.models import ContextSnapshot, Intent, NextAction, Route, SessionState, Slot
from techjam_agent.contracts import Candidate, RankedCandidate
from techjam_agent.contracts_v2 import RankingResultV2, RetrievalResultV2


def examples():
    parsed = IntentRouter().understand("blue instead")
    operations = (
        SlotUpdate("color", "set", ("blue",), "hard", evidence="blue instead"),
        SlotUpdate("price_max", "clear", evidence="ignore the budget"),
        SlotUpdate("color", "exclude", ("black",), evidence="not black"),
        SlotUpdate("color", "remove_exclusion", ("black",), evidence="black is also fine"),
    )
    # Independent fixtures: the state below is NOT obtained by applying all
    # four operation examples (which describe different user messages).
    session = SessionState("example", turn_id=3, intent=Intent.BUYING, intent_confidence=0.9)
    session.hard_slots = {"color": Slot("blue", 3), "price_max": Slot(50.0, 1)}
    session.rejected_values = {"color": ["black"]}
    old_snapshot = ContextSnapshot(
        "blue instead", Intent.BUYING, Route.BUYING_FILTER, NextAction.RETRIEVE_BUYING,
        {"color": "blue", "price_max": 50.0}, {}, {"color": ["black"]}, {}, None, 50,
        "Blue item, at most $50; exclude black.",
    )
    state = StateSnapshotV2.from_legacy(old_snapshot, session=session, state_version=7)
    candidates = tuple(Candidate(f"EXAMPLE_ONLY_{i}", i + 1, {"fixture": i + 1}, {"title": f"Example {i}"}) for i in range(2))
    retrieval = RetrievalResultV2("example:3:7", "example", 3, 7, 50, candidates, state_snapshot=state.to_dict())
    ranked = RankingResultV2("example:3:7", "example", 3, 7, tuple(RankedCandidate(c.parent_asin, i + 1, 1 / (i + 1), ()) for i, c in enumerate(candidates)), "fixture")
    ranked.validate_against(retrieval, top_k=10)
    empty = replace(retrieval, candidate_set_id="empty-example", candidates=())
    return {
        "scope": "Schema fixtures only. EXAMPLE_ONLY IDs never enter the real catalog or evaluation.",
        "current_router_handoff": parsed.to_state_handoff(session_id="example", turn=3),
        "four_independent_operation_examples": [asdict(item) for item in operations],
        "explicit_set_handoff_fixture": replace(parsed, slot_updates=(operations[0],)).to_state_handoff(session_id="example", turn=3),
        "state_snapshot_v2": state.to_dict(),
        "two_candidate_result_v2": retrieval.to_dict(),
        "two_recommendation_ranking_v2": ranked.to_dict(),
        "empty_candidate_result_v2": empty.to_dict(),
    }


if __name__ == "__main__":
    print(json.dumps(examples(), ensure_ascii=False, indent=2, allow_nan=False))
