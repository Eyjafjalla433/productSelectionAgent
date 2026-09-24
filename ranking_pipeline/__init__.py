"""Ranking and clarification policy package for the D component."""

from __future__ import annotations

import sys
from pathlib import Path


# Make the package runnable from either the repository root or from
# retrieval-and-reranking. This package depends on the sibling techjam_agent
# package, while techjam_agent exposes the repository root in its own import
# bootstrap.
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_TECHJAM_ROOT = _REPOSITORY_ROOT / "retrieval-and-reranking"
if str(_TECHJAM_ROOT) not in sys.path:
    sys.path.insert(0, str(_TECHJAM_ROOT))
_STATE_MEMORY_ROOT = _REPOSITORY_ROOT / "conversation-state-memory" / "src"
if str(_STATE_MEMORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_STATE_MEMORY_ROOT))
_INTENT_ROOT = _REPOSITORY_ROOT / "intent-recognition"
if str(_INTENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_INTENT_ROOT))

from ranking_pipeline.contextual_ranking import (
    CandidatePoolMetrics,
    ConstraintConflict,
    HybridContextualReranker,
    LocalQwenRanker,
    PolicyDecision,
    RecommendationClarificationPolicy,
    assess_candidate_pool,
    choose_clarification_attribute,
    choose_rerank_strategy,
)
from ranking_pipeline.distribution_alignment import (
    DistributionSummary,
    build_aligned_training_examples,
    summarize_examples,
    summarize_synthetic_rankings,
)
from ranking_pipeline.prompt import (
    LLMRankResult,
    build_rerank_prompt,
    estimate_prompt_tokens,
    parse_rerank_output,
)
from ranking_pipeline.qwen_reranker import Qwen3Reranker
from ranking_pipeline.memory_context import (
    intent_to_context,
    intent_to_requirements,
    merge_profile_with_snapshot,
    snapshot_to_requirements,
)

__all__ = [
    "ConstraintConflict",
    "CandidatePoolMetrics",
    "DistributionSummary",
    "HybridContextualReranker",
    "LLMRankResult",
    "LocalQwenRanker",
    "PolicyDecision",
    "Qwen3Reranker",
    "RecommendationClarificationPolicy",
    "assess_candidate_pool",
    "choose_clarification_attribute",
    "choose_rerank_strategy",
    "build_rerank_prompt",
    "build_aligned_training_examples",
    "estimate_prompt_tokens",
    "intent_to_context",
    "intent_to_requirements",
    "merge_profile_with_snapshot",
    "parse_rerank_output",
    "snapshot_to_requirements",
    "summarize_examples",
    "summarize_synthetic_rankings",
]
