"""Offline-first contextual reranker and clarification policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from techjam_agent.contracts import (
    Candidate,
    CandidateSet,
    RankedCandidate,
    RerankResult,
    Requirements,
)
from ranking_pipeline.context import ShortTermSummary, profile_features
from ranking_pipeline.memory_context import merge_profile_with_snapshot
from ranking_pipeline.prompt import (
    LLMRankResult,
    build_rerank_prompt,
    estimate_prompt_tokens,
    parse_rerank_output,
)
from ranking_pipeline.qwen_reranker import Qwen3Reranker, product_text
from techjam_agent.query import parse_intent, tokenize
from techjam_agent.ranking import LockedWeightedRrfTop10Reranker
from techjam_agent.retrieval import VISIBLE_FIELDS, _text


@dataclass(frozen=True)
class ConstraintConflict:
    parent_asin: str
    missing_terms: tuple[str, ...]


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    ask_attribute: str | None
    message: str
    reason: str


@dataclass(frozen=True)
class CandidatePoolMetrics:
    is_over_general: bool
    top_score: float
    second_score: float
    score_spread: float
    hard_hit_rate: float
    candidate_count: int
    strategy: str


ALLOWED_ATTRIBUTES = frozenset(
    {
        "category",
        "material",
        "color",
        "size",
        "style",
        "brand",
        "budget",
        "feature",
        "use_case",
        "other",
    }
)

CLARIFICATION_MESSAGES = {
    "material": "Do you have a material preference?",
    "color": "Do you have a color preference?",
    "size": "Do you need a specific size?",
    "style": "Is there a particular style you prefer?",
    "brand": "Do you have a brand preference?",
    "budget": "What is your budget range?",
    "feature": "Is there a required feature?",
    "use_case": "What will you use it for?",
    "other": "Do you have any other requirements?",
}


CLARIFICATION_ATTRIBUTE_ORDER = (
    "material",
    "color",
    "size",
    "style",
    "brand",
    "budget",
    "feature",
    "use_case",
    "other",
)


def _candidate_tokens(candidate: Candidate) -> set[str]:
    text = " ".join(_text(candidate.product.get(field)) for field in VISIBLE_FIELDS)
    return set(tokenize(text))


def evaluate_hard_constraints(
    candidate: Candidate,
    requirements: Requirements,
) -> tuple[str, ...]:
    """Return hard terms missing from the candidate's public product snapshot."""

    plan = parse_intent(
        requirements.category,
        requirements.hard_constraints,
        requirements.soft_preferences,
    )
    if not plan.hard_terms:
        return ()
    tokens = _candidate_tokens(candidate)
    return tuple(term for term in plan.hard_terms if term not in tokens)


def filter_hard_conflicts(
    candidates: Sequence[Candidate],
    requirements: Requirements,
    *,
    min_keep: int = 15,
) -> tuple[tuple[Candidate, ...], tuple[ConstraintConflict, ...]]:
    """Remove only fully conflicted candidates while preserving enough options."""

    evaluated = [
        (candidate, evaluate_hard_constraints(candidate, requirements))
        for candidate in candidates
    ]
    fully_conflicted = [
        (candidate, missing)
        for candidate, missing in evaluated
        if missing and len(missing) == len(
            parse_intent(
                requirements.category,
                requirements.hard_constraints,
                requirements.soft_preferences,
            ).hard_terms
        )
    ]
    if len(candidates) - len(fully_conflicted) >= min_keep:
        removed_ids = {candidate.parent_asin for candidate, _ in fully_conflicted}
        kept = tuple(candidate for candidate in candidates if candidate.parent_asin not in removed_ids)
    else:
        kept = tuple(candidates)
        fully_conflicted = []
    conflicts = tuple(
        ConstraintConflict(parent_asin=candidate.parent_asin, missing_terms=missing)
        for candidate, missing in fully_conflicted
    )
    return kept, conflicts


def hard_constraint_hit_rate(
    candidates: Sequence[Candidate],
    requirements: Requirements,
) -> float:
    """Return the fraction of candidates that satisfy every parsed hard term."""

    if not candidates:
        return 0.0
    plan = parse_intent(
        requirements.category,
        requirements.hard_constraints,
        requirements.soft_preferences,
    )
    if not plan.hard_terms:
        return 1.0
    hits = sum(
        1
        for candidate in candidates
        if not evaluate_hard_constraints(candidate, requirements)
    )
    return hits / len(candidates)


def choose_rerank_strategy(
    *,
    has_model: bool,
    turn: int,
    selected_count: int,
    top_k: int,
    hard_hit_rate: float,
) -> str:
    """Select the cheapest reliable reranking path for the current context."""

    if not has_model:
        return "hybrid"
    if turn >= 10:
        return "locked"
    if hard_hit_rate < 0.5:
        return "locked"
    if selected_count > top_k * 3 and hard_hit_rate < 0.8:
        return "locked"
    return "hybrid"


def choose_clarification_attribute(
    requirements: Requirements,
    *,
    asked_attributes: Sequence[str] = (),
    over_general: bool = False,
) -> str:
    """Pick the highest-value missing attribute without duplicating prior asks."""

    asked = set(asked_attributes)
    if over_general:
        for attribute in ("material", "color", "size", "style", "brand", "budget"):
            if attribute not in asked:
                return attribute
    disclosed_text = " ".join(
        (requirements.category, *requirements.hard_constraints, *requirements.soft_preferences)
    ).lower()
    for attribute in CLARIFICATION_ATTRIBUTE_ORDER:
        if attribute in asked:
            continue
        if attribute == "other":
            continue
        if attribute not in disclosed_text:
            return attribute
    if "other" not in asked:
        return "other"
    return "material"


def assess_candidate_pool(
    ranked_candidates: Sequence[RankedCandidate],
    *,
    hard_hit_rate: float,
    candidate_count: int,
    strategy: str,
) -> CandidatePoolMetrics:
    if not ranked_candidates:
        return CandidatePoolMetrics(True, 0.0, 0.0, 0.0, hard_hit_rate, candidate_count, strategy)
    top_score = ranked_candidates[0].score
    second_score = ranked_candidates[1].score if len(ranked_candidates) > 1 else 0.0
    score_spread = max(0.0, top_score - second_score)
    normalized_margin = score_spread / max(abs(top_score), 1e-9)
    is_over_general = (
        normalized_margin < 0.18
        and hard_hit_rate < 0.85
    ) or (
        candidate_count > 10
        and hard_hit_rate < 0.65
    )
    return CandidatePoolMetrics(
        is_over_general=is_over_general,
        top_score=top_score,
        second_score=second_score,
        score_spread=score_spread,
        hard_hit_rate=hard_hit_rate,
        candidate_count=candidate_count,
        strategy=strategy,
    )


class LocalQwenRanker:
    """Lazy local instruction-model ranker with deterministic JSON decoding."""

    def __init__(
        self,
        model_name_or_path: str | None = None,
        *,
        device: str | None = None,
        load_in_4bit: bool = False,
        max_new_tokens: int = 256,
    ) -> None:
        self.model_name_or_path = (
            model_name_or_path
            or os.environ.get("TECHJAM_RERANKER_MODEL")
            or "Qwen/Qwen2.5-3B-Instruct"
        )
        self.device = device or os.environ.get("TECHJAM_RERANKER_DEVICE")
        self.load_in_4bit = load_in_4bit
        self.max_new_tokens = max_new_tokens
        self._model: Any | None = None
        self._tokenizer: Any | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None and self._tokenizer is not None

    def load(self) -> None:
        if self.loaded:
            return
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        model_kwargs: dict[str, Any] = {}
        if self.load_in_4bit:
            model_kwargs["load_in_4bit"] = True
        if self.device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        if self.device == "cuda" and not self.load_in_4bit:
            model_kwargs["torch_dtype"] = torch.float16
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name_or_path)
        self._model = AutoModelForCausalLM.from_pretrained(
            self.model_name_or_path,
            **model_kwargs,
        )
        if not self.load_in_4bit:
            self._model.to(self.device)
        self._model.eval()

    def rank(
        self,
        prompt: str,
        *,
        allowed_ids: Sequence[str],
        top_k: int,
    ) -> LLMRankResult:
        self.load()
        import torch

        assert self._model is not None and self._tokenizer is not None
        inputs = self._tokenizer([prompt], return_tensors="pt")
        inputs = {name: value.to(self.device) for name, value in inputs.items()}
        with torch.inference_mode():
            outputs = self._model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
            )
        input_length = inputs["input_ids"].shape[-1]
        generated = outputs[0][input_length:]
        text = self._tokenizer.decode(generated, skip_special_tokens=True)
        return parse_rerank_output(text, allowed_ids=allowed_ids, top_k=top_k)


class HybridContextualReranker:
    """Hard-filter candidates, pre-rank with locked RRF, then let an LLM rank a small set."""

    def __init__(
        self,
        llm_ranker: LocalQwenRanker | None = None,
        *,
        top_n: int = 20,
        min_keep: int = 15,
        profile_weight: float = 0.05,
        pointwise_weight: float = 0.35,
        hard_constraint_penalty: float = 0.20,
    ) -> None:
        try:
            pointwise_weight = float(os.environ["TECHJAM_POINTWISE_WEIGHT"])
        except (KeyError, TypeError, ValueError):
            pass
        try:
            hard_constraint_penalty = float(os.environ["TECHJAM_HARD_PENALTY"])
        except (KeyError, TypeError, ValueError):
            pass
        self.llm_ranker = llm_ranker
        self.top_n = top_n
        self.min_keep = min_keep
        self.profile_weight = profile_weight
        self.pointwise_weight = pointwise_weight
        self.hard_constraint_penalty = hard_constraint_penalty
        self.fallback = LockedWeightedRrfTop10Reranker()
        self._profiles: dict[str, Mapping[str, Any]] = {}
        self._short_term: dict[str, ShortTermSummary] = {}
        self._asked_attributes: dict[str, tuple[str, ...]] = {}
        self._context_snapshots: dict[str, Any] = {}
        self._intent_contexts: dict[str, Mapping[str, Any]] = {}
        self.last_preselected_ids: tuple[str, ...] = ()
        self.last_model_result: LLMRankResult | None = None
        self.last_conflicts: tuple[ConstraintConflict, ...] = ()
        self.last_hard_missing: dict[str, tuple[str, ...]] = {}
        self.last_pool_metrics: CandidatePoolMetrics | None = None
        self.last_strategy: str = "hybrid"
        self.last_prompt_candidate_count: int = 0
        self.last_prompt_chars: int = 0
        self.last_prompt_tokens: int = 0

    def set_session_context(
        self,
        session_id: str,
        user_profile: Mapping[str, Any] | None,
        *,
        requirements: Requirements | None = None,
        short_term: ShortTermSummary | None = None,
        asked_attributes: Sequence[str] = (),
        context_snapshot: Any | None = None,
        intent_context: Mapping[str, Any] | None = None,
    ) -> None:
        self._profiles[session_id] = merge_profile_with_snapshot(
            user_profile,
            context_snapshot,
        )
        self._context_snapshots[session_id] = context_snapshot
        self._intent_contexts[session_id] = dict(intent_context or {})
        if short_term is not None:
            self._short_term[session_id] = short_term
        elif requirements is not None:
            self._short_term[session_id] = ShortTermSummary(
                requirements=requirements,
                clarification_turns=(),
                override_turns=(),
            )
        self._asked_attributes[session_id] = tuple(asked_attributes)

    def _profile_boost(
        self,
        candidates: Mapping[str, Candidate],
        *,
        session_id: str,
    ) -> dict[str, float]:
        profile = profile_features(self._profiles.get(session_id))
        if not profile.preference_tags:
            return {}
        profile_terms = {
            term
            for value in profile.preference_tags
            for term in tokenize(str(value))
        }
        if not profile_terms:
            return {}
        boosts: dict[str, float] = {}
        for parent_asin, candidate in candidates.items():
            overlap = len(profile_terms & _candidate_tokens(candidate))
            if overlap:
                boosts[parent_asin] = self.profile_weight * overlap
        return boosts

    def rerank(self, candidate_set: CandidateSet, *, top_k: int) -> RerankResult:
        candidates = list(candidate_set.candidates)
        kept, conflicts = filter_hard_conflicts(
            candidates,
            candidate_set.requirements,
            min_keep=self.min_keep,
        )
        self.last_conflicts = conflicts
        kept_ids = {candidate.parent_asin for candidate in kept}
        all_ranked = self.fallback.rerank(candidate_set, top_k=len(candidates))
        fallback_scores = {
            ranked.parent_asin: ranked.score for ranked in all_ranked.ranked_candidates
        }
        ordered_ids = [
            ranked.parent_asin for ranked in all_ranked.ranked_candidates
        ]
        preselected_ids = [
            parent_asin for parent_asin in ordered_ids if parent_asin in kept_ids
        ]
        preselected_ids.extend(
            parent_asin for parent_asin in ordered_ids if parent_asin not in kept_ids
        )
        preselected_ids = tuple(preselected_ids[: self.top_n])
        self.last_preselected_ids = preselected_ids
        self.last_prompt_candidate_count = 0
        self.last_prompt_chars = 0
        self.last_prompt_tokens = 0

        candidate_by_id = {candidate.parent_asin: candidate for candidate in candidates}
        profile_boosts = self._profile_boost(
            candidate_by_id,
            session_id=candidate_set.session_id,
        )
        selected_candidates = [candidate_by_id[parent_asin] for parent_asin in preselected_ids]
        missing_by_id = {
            candidate.parent_asin: evaluate_hard_constraints(
                candidate,
                candidate_set.requirements,
            )
            for candidate in selected_candidates
        }
        self.last_hard_missing = missing_by_id
        plan = parse_intent(
            candidate_set.requirements.category,
            candidate_set.requirements.hard_constraints,
            candidate_set.requirements.soft_preferences,
        )
        hard_term_count = max(1, len(plan.hard_terms))
        hard_penalty_by_id = {
            parent_asin: self.hard_constraint_penalty
            * (len(missing_by_id[parent_asin]) / hard_term_count)
            for parent_asin in preselected_ids
        }

        preselected_ids = tuple(
            sorted(
                preselected_ids,
                key=lambda parent_asin: (
                    -(fallback_scores.get(parent_asin, 0.0)
                      + profile_boosts.get(parent_asin, 0.0)
                      - hard_penalty_by_id[parent_asin]),
                    parent_asin,
                ),
            )
        )
        self.last_preselected_ids = preselected_ids
        selected_candidates = [candidate_by_id[parent_asin] for parent_asin in preselected_ids]
        hard_hit_rate = hard_constraint_hit_rate(
            selected_candidates,
            candidate_set.requirements,
        )
        strategy = choose_rerank_strategy(
            has_model=self.llm_ranker is not None,
            turn=candidate_set.turn,
            selected_count=len(selected_candidates),
            top_k=top_k,
            hard_hit_rate=hard_hit_rate,
        )
        self.last_strategy = strategy
        prompt_candidate_limit = min(self.top_n, max(top_k * 2, 10))
        prompt_candidate_ids = tuple(preselected_ids[:prompt_candidate_limit])
        prompt_candidates = [
            candidate_by_id[parent_asin] for parent_asin in prompt_candidate_ids
        ]
        model_result: LLMRankResult | None = None
        pointwise_scores: dict[str, float] = {}
        if self.llm_ranker is not None and strategy == "hybrid":
            self.last_prompt_candidate_count = len(prompt_candidate_ids)
            try:
                if isinstance(self.llm_ranker, Qwen3Reranker):
                    pointwise_scores = self.llm_ranker.score_candidates(
                        candidate_set.requirements,
                        prompt_candidates,
                        user_profile=self._profiles.get(candidate_set.session_id),
                    )
                else:
                    prompt = build_rerank_prompt(
                        candidate_set.requirements,
                        prompt_candidates,
                        top_k=top_k,
                        user_profile=self._profiles.get(candidate_set.session_id),
                    )
                    self.last_prompt_chars = len(prompt)
                    self.last_prompt_tokens = estimate_prompt_tokens(prompt)
                    model_result = self.llm_ranker.rank(
                        prompt,
                        allowed_ids=prompt_candidate_ids,
                        top_k=top_k,
                    )
            except Exception:
                model_result = None
        self.last_model_result = model_result

        if pointwise_scores:
            fallback_values = [
                fallback_scores.get(parent_asin, 0.0)
                for parent_asin in prompt_candidate_ids
            ]
            fallback_min = min(fallback_values)
            fallback_max = max(fallback_values)
            fallback_norm = {
                parent_asin: (
                    (fallback_scores.get(parent_asin, 0.0) - fallback_min)
                    / max(fallback_max - fallback_min, 1e-9)
                )
                for parent_asin in prompt_candidate_ids
            }
            fused_scores = {
                parent_asin: (
                    fallback_norm[parent_asin]
                    + self.pointwise_weight * pointwise_scores.get(parent_asin, 0.0)
                    - hard_penalty_by_id[parent_asin]
                )
                for parent_asin in prompt_candidate_ids
            }
            ranked_ids = sorted(
                prompt_candidate_ids,
                key=lambda parent_asin: (-fused_scores[parent_asin], parent_asin),
            )[:top_k]
            ranked_scores = tuple(pointwise_scores[parent_asin] for parent_asin in ranked_ids)
            ranked_reasons = tuple(
                f"relevance {pointwise_scores[parent_asin]:.3f}; "
                f"{product_text(candidate_by_id[parent_asin], max_chars=90)}"
                for parent_asin in ranked_ids
            )
            top_score = max(pointwise_scores[parent_asin] for parent_asin in ranked_ids)
            model_result = LLMRankResult(
                ranked_ids=tuple(ranked_ids),
                constraint_conflicts=(),
                confidence=max(0.0, min(1.0, float(top_score))),
                need_clarification=top_score < getattr(
                    self.llm_ranker, "clarification_threshold", 0.45
                ),
                scores=ranked_scores,
                reasons=ranked_reasons,
            )
            self.last_model_result = model_result

        ranked_ids: list[str]
        if model_result is not None:
            ranked_ids = list(model_result.ranked_ids)
            ranked_ids.extend(
                parent_asin
                for parent_asin in preselected_ids
                if parent_asin not in ranked_ids
            )
            original_positions = {
                parent_asin: index for index, parent_asin in enumerate(ranked_ids)
            }
            ranked_ids = sorted(
                ranked_ids,
                key=lambda parent_asin: (
                    int(len(missing_by_id.get(parent_asin, ())) > 0),
                    original_positions[parent_asin],
                ),
            )
            ranked_ids = ranked_ids[:top_k]
        else:
            ranked_ids = list(preselected_ids[:top_k])

        confidence = model_result.confidence if model_result is not None else 0.0
        model_scores = dict(
            zip(model_result.ranked_ids, model_result.scores)
        ) if model_result is not None and len(model_result.scores) == len(model_result.ranked_ids) else {}
        model_reasons = dict(
            zip(model_result.ranked_ids, model_result.reasons)
        ) if model_result is not None and len(model_result.reasons) == len(model_result.ranked_ids) else {}
        ranked_candidates = tuple(
            RankedCandidate(
                parent_asin=parent_asin,
                rank=rank_index,
                score=(
                    model_scores.get(parent_asin)
                    if parent_asin in model_scores
                    else (1.0 / rank_index) * (confidence if model_result else 1.0)
                ),
                evidence=(
                    f"fallback_score:{fallback_scores.get(parent_asin, 0.0):.6f}",
                    f"preselect_rank:{preselected_ids.index(parent_asin) + 1}",
                    f"strategy:{strategy}",
                    f"hard_missing:{','.join(missing_by_id.get(parent_asin, ())) or 'none'}",
                    f"reason:{model_reasons.get(parent_asin, 'locked rank')}",
                ),
            )
            for rank_index, parent_asin in enumerate(ranked_ids, start=1)
        )
        self.last_pool_metrics = assess_candidate_pool(
            ranked_candidates,
            hard_hit_rate=hard_hit_rate,
            candidate_count=len(selected_candidates),
            strategy=strategy,
        )
        return RerankResult(
            candidate_set_id=candidate_set.candidate_set_id,
            ranked_candidates=ranked_candidates,
        )


class RecommendationClarificationPolicy:
    """Decide whether the current ranked list is strong enough to recommend."""

    def __init__(
        self,
        *,
        confidence_threshold: float = 0.55,
        margin_threshold: float = 0.12,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.margin_threshold = margin_threshold

    def decide(
        self,
        ranked_candidates: Sequence[RankedCandidate],
        *,
        model_result: LLMRankResult | None,
        conflicts: Sequence[ConstraintConflict],
        requirements: Requirements | None = None,
        asked_attributes: Sequence[str] = (),
        pool_metrics: CandidatePoolMetrics | None = None,
    ) -> PolicyDecision:
        def question(attribute: str, reason: str) -> PolicyDecision:
            return PolicyDecision(
                "clarify",
                attribute,
                CLARIFICATION_MESSAGES[attribute],
                reason,
            )

        if model_result is not None and model_result.need_clarification:
            attribute = choose_clarification_attribute(
                requirements or Requirements("", (), ()),
                asked_attributes=asked_attributes,
                over_general=True,
            )
            return question(attribute, "The reranking model requested clarification.")
        if conflicts:
            attribute = choose_clarification_attribute(
                requirements or Requirements("", (), ()),
                asked_attributes=asked_attributes,
                over_general=False,
            )
            return question(attribute, "One or more candidates violate a disclosed hard constraint.")
        if not ranked_candidates:
            return question("other", "The candidate pool is empty.")
        if pool_metrics is not None and pool_metrics.is_over_general:
            attribute = choose_clarification_attribute(
                requirements or Requirements("", (), ()),
                asked_attributes=asked_attributes,
                over_general=True,
            )
            return question(attribute, "The candidate pool is over-general.")
        top_score = ranked_candidates[0].score
        second_score = ranked_candidates[1].score if len(ranked_candidates) > 1 else 0.0
        margin = (top_score - second_score) / max(abs(top_score), 1e-9)
        confidence = model_result.confidence if model_result is not None else 1.0
        if confidence < self.confidence_threshold or margin < self.margin_threshold:
            attribute = choose_clarification_attribute(
                requirements or Requirements("", (), ()),
                asked_attributes=asked_attributes,
                over_general=True,
            )
            return question(attribute, "Top candidates are not sufficiently separated.")
        return PolicyDecision(
            "recommend",
            None,
            "Here are the best matches for all requirements you shared.",
            "The top ranked candidate is confidently separated.",
        )
