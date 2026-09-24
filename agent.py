"""Official submission entry: `from agent import Agent`.

The scoring agent reads only the product catalog and reset/respond inputs.
Evaluation labels and the simulator are loaded only by submission_tools.evaluate.
"""
from pathlib import Path
import os

from shopping_agent import FinalAgent


class Agent(FinalAgent):
    """Offline CPU submission; model weights, API credentials and GPU not required."""

    def __init__(self, catalog_path=None, *, trace_enabled=False, ranking_mode=None, orchestration_mode="score_compat", clarification_mode=None, requirement_enhancer=None):
        configured = catalog_path or os.environ.get("SHOPPING_CATALOG_PATH")
        self.catalog_path = Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parent / "competition_kit/data/catalog.jsonl"
        if not self.catalog_path.is_file():
            raise FileNotFoundError(
                "Official catalog missing. Run `python -m submission_tools.prepare_data`, "
                "or set SHOPPING_CATALOG_PATH to the organizer-provided catalog.jsonl."
            )
        if clarification_mode is None and orchestration_mode == "score_compat":
            clarification_mode = "fixed_two_dynamic"
        super().__init__(self.catalog_path, ranking_mode=ranking_mode, orchestration_mode=orchestration_mode,
                         clarification_mode=clarification_mode, trace_enabled=trace_enabled,
                         requirement_enhancer=requirement_enhancer)

    def reset(self, session_id: str, user_profile: dict) -> None:
        return super().reset(session_id, user_profile)

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int = 10) -> dict:
        return super().respond(session_id, user_message, turn, top_k)
