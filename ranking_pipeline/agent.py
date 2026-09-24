"""Ranking-agent adapter owned by the ranking pipeline.

This module keeps the retrieval-and-reranking package unchanged. It subclasses
the official ``Agent`` and injects the contextual reranker plus the short-term
clarification state needed by component 4/D. The official evaluator can then be
called directly with this adapter by ``ranking_pipeline.evaluate_agent``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from techjam_agent.agent import Agent as BaseAgent
from techjam_agent.dialogue import RequirementsCollector as BaseRequirementsCollector
from techjam_agent.ranking import LockedWeightedRrfTop10Reranker

from ranking_pipeline.context import ShortTermSummary, parse_override_message
from ranking_pipeline.contextual_ranking import (
    HybridContextualReranker,
    RecommendationClarificationPolicy,
)
from ranking_pipeline.memory_context import (
    intent_to_context,
)
from ranking_pipeline.override_aware_agent import OverrideAwareRequirementsCollector
from ranking_pipeline.qwen_reranker import Qwen3Reranker


@dataclass
class ContextualRequirementsCollector(BaseRequirementsCollector):
    """The official collector plus profile and intent-override bookkeeping."""

    user_profile: dict = field(default_factory=dict)
    override_turns: list[int] = field(default_factory=list)

    def observe(self, user_message: str, turn: int) -> None:
        if turn == 1:
            super().observe(user_message, turn)
            return
        replacements = parse_override_message(user_message)
        if replacements:
            self.hard_constraints = list(
                dict.fromkeys((*self.hard_constraints, *replacements))
            )
            replacement_set = set(replacements)
            self.soft_preferences = [
                value for value in self.soft_preferences if value not in replacement_set
            ]
            self.override_turns.append(turn)
            return
        super().observe(user_message, turn)

    def short_term_summary(self) -> ShortTermSummary:
        return ShortTermSummary(
            requirements=self.requirements(),
            clarification_turns=tuple(str(index) for index in range(self.other_reply_count)),
            override_turns=tuple(self.override_turns),
        )

    def context_payload(self) -> dict[str, Any]:
        return {
            "requirements": self.requirements(),
            "user_profile": self.user_profile,
            "override_turns": tuple(self.override_turns),
            "other_reply_count": self.other_reply_count,
        }


class RankingAgent(BaseAgent):
    """Official Agent contract with ranking-pipeline context injected."""

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        retrieval_mode: str = "lite",
        reranker_mode: str = "locked",
        reranker_model: str | Path | None = None,
        policy_enabled: bool = False,
        use_state_memory: bool = False,
        use_intent_router: bool = False,
        clarification_turn_limit: int = 2,
    ) -> None:
        from techjam_agent.retrieval import (
            ExactDenseTop50CandidateGenerator,
            LiteTop50CandidateGenerator,
        )

        if retrieval_mode == "exact":
            candidate_generator = ExactDenseTop50CandidateGenerator(catalog_path)
        elif retrieval_mode == "lite":
            candidate_generator = LiteTop50CandidateGenerator(catalog_path)
        else:
            raise ValueError("retrieval_mode must be 'exact' or 'lite'")

        if reranker_mode == "local":
            reranker = HybridContextualReranker(
                llm_ranker=Qwen3Reranker(str(reranker_model) if reranker_model else None)
            )
        elif reranker_mode == "hybrid":
            reranker = HybridContextualReranker(llm_ranker=None)
        elif reranker_mode == "locked":
            reranker = LockedWeightedRrfTop10Reranker()
        else:
            raise ValueError("reranker_mode must be 'locked', 'hybrid', or 'local'")
        if type(clarification_turn_limit) is not int or not 0 <= clarification_turn_limit < 10:
            raise ValueError("clarification_turn_limit must be an integer from 0 to 9")

        super().__init__(catalog_path, candidate_generator=candidate_generator, reranker=reranker)
        self._asked_attributes: dict[str, list[str]] = {}
        self._policy_enabled = policy_enabled
        self._clarification_turn_limit = clarification_turn_limit
        self._memory_snapshots: dict[str, Any] = {}
        self._intent_contexts: dict[str, dict[str, Any]] = {}
        self._intent_results: dict[str, Any] = {}
        self._state_memory = None
        if use_state_memory:
            from state_memory import StateMemoryManager

            self._state_memory = StateMemoryManager()
        self._intent_router = None
        if use_intent_router:
            from intent_router import IntentRouter, load_catalog_brands, load_catalog_categories

            self._intent_router = IntentRouter(
                known_brands=load_catalog_brands(Path(catalog_path)),
                known_categories=load_catalog_categories(Path(catalog_path)),
            )

    def reset(self, session_id: str, user_profile: dict) -> None:
        collector = OverrideAwareRequirementsCollector(user_profile=user_profile)
        self._sessions[session_id] = collector
        self._asked_attributes[session_id] = []
        self._memory_snapshots.pop(session_id, None)
        self._intent_contexts.pop(session_id, None)
        self._intent_results.pop(session_id, None)
        if self._state_memory is not None:
            self._state_memory.sessions.pop(session_id, None)
        self._configure_reranker(session_id, collector)

    def _configure_reranker(self, session_id: str, collector: ContextualRequirementsCollector) -> None:
        set_session_context = getattr(self.reranker, "set_session_context", None)
        if set_session_context is None:
            return
        set_session_context(
            session_id,
            collector.user_profile,
            requirements=collector.requirements(),
            asked_attributes=tuple(self._asked_attributes.get(session_id, ())),
            short_term=collector.short_term_summary(),
            context_snapshot=self._memory_snapshots.get(session_id),
            intent_context=self._intent_contexts.get(session_id),
        )

    def _update_external_context(self, session_id: str, user_message: str) -> None:
        """Feed the sibling state/intent modules without changing retrieval code."""

        if self._state_memory is not None:
            snapshot = self._state_memory.update(
                session_id=session_id,
                user_id=session_id,
                utterance=user_message,
            )
            self._memory_snapshots[session_id] = snapshot
        if self._intent_router is not None:
            intent_result = self._intent_router.understand(user_message)
            self._intent_results[session_id] = intent_result
            self._intent_contexts[session_id] = intent_to_context(intent_result)

    def _record_asked(self, session_id: str, attribute: str | None) -> None:
        if attribute is None:
            return
        asked = self._asked_attributes.setdefault(session_id, [])
        if attribute not in asked:
            asked.append(attribute)

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
        self._update_external_context(session_id, user_message)
        self._configure_reranker(session_id, collector)
        if turn <= self._clarification_turn_limit:
            self._record_asked(session_id, "other")
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
        if self._policy_enabled and turn < 10:
            decision = self._policy_decision(result, session_id)
            if decision is not None and decision.action == "clarify":
                self._record_asked(session_id, decision.ask_attribute)
                return {
                    "message": decision.message,
                    "ask_attribute": decision.ask_attribute,
                    "recommendations": [],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                }
        return {
            "message": "Here are the best matches for all requirements you shared.",
            "ask_attribute": None,
            "recommendations": [
                {"parent_asin": candidate.parent_asin}
                for candidate in result.ranked_candidates
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }

    def _policy_decision(self, result, session_id: str):
        collector = self._sessions.get(session_id)
        model_result = getattr(self.reranker, "last_model_result", None)
        conflicts = getattr(self.reranker, "last_conflicts", ())
        pool_metrics = getattr(self.reranker, "last_pool_metrics", None)
        return RecommendationClarificationPolicy().decide(
            result.ranked_candidates,
            model_result=model_result,
            conflicts=conflicts,
            requirements=collector.requirements() if collector is not None else None,
            asked_attributes=tuple(self._asked_attributes.get(session_id, ())),
            pool_metrics=pool_metrics,
        )


__all__ = ["ContextualRequirementsCollector", "RankingAgent"]
