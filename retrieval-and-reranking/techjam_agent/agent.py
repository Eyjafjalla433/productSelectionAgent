"""Official Agent adapter that composes Top-50 generation and Top-10 reranking."""

from __future__ import annotations

import os
from pathlib import Path

from techjam_agent.contracts import Top10Reranker, Top50CandidateGenerator
from techjam_agent.dialogue import RequirementsCollector


class Agent:
    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        candidate_generator: Top50CandidateGenerator | None = None,
        reranker: Top10Reranker | None = None,
    ) -> None:
        if candidate_generator is None:
            from techjam_agent.retrieval import (
                ExactDenseTop50CandidateGenerator,
                LiteTop50CandidateGenerator,
            )

            mode = os.environ.get("TECHJAM_MODE", "exact").lower()
            if mode == "exact":
                candidate_generator = ExactDenseTop50CandidateGenerator(catalog_path)
            elif mode == "lite":
                candidate_generator = LiteTop50CandidateGenerator(catalog_path)
            else:
                raise ValueError("TECHJAM_MODE must be 'exact' or 'lite'")
        if reranker is None:
            from techjam_agent.ranking import LockedWeightedRrfTop10Reranker

            reranker = LockedWeightedRrfTop10Reranker()
        self.candidate_generator = candidate_generator
        self.reranker = reranker
        self._sessions: dict[str, RequirementsCollector] = {}

    def reset(self, session_id: str, user_profile: dict) -> None:
        del user_profile
        self._sessions[session_id] = RequirementsCollector()

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        collector = self._sessions.get(session_id)
        if collector is None:
            raise RuntimeError("reset must be called before respond")
        collector.observe(user_message, turn)
        if turn <= 2:
            return {
                "message": "Please share any other requirements that matter.",
                "ask_attribute": "other",
                "recommendations": [],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }
        candidate_set = self.candidate_generator.generate(
            collector.requirements(), session_id=session_id, turn=turn
        )
        result = self.reranker.rerank(candidate_set, top_k=top_k)
        result.validate_against(candidate_set, top_k=top_k)
        return {
            "message": "Here are the best matches for all requirements you shared.",
            "ask_attribute": None,
            "recommendations": [
                {"parent_asin": candidate.parent_asin}
                for candidate in result.ranked_candidates
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
