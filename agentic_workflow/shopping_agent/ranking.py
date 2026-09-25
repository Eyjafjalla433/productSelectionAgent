"""Variable-size adapter for module 4's locked CPU scoring plus soft context."""
from dataclasses import dataclass, replace

from techjam_agent.contracts_v2 import RankingResultV2
from techjam_agent.ranking import LockedWeightedRrfTop10Reranker
from techjam_agent.retrieval import _text
from ranking_pipeline.contextual_ranking import HybridContextualReranker
from .retrieval import size_matches, style_matches, terms


@dataclass(frozen=True)
class ScoringInput:
    """Structural input to the existing scorer; deliberately NOT a Top50 set."""
    candidate_set_id: str
    requirements: object
    candidates: tuple
    session_id: str
    turn: int


class StateAwareReranker:
    def __init__(self, mode="hybrid"):
        if mode not in {"hybrid", "locked"}:
            raise ValueError("ranking mode must be hybrid or locked")
        self.mode = mode
        # RRF scores are ~0.01-0.05: the legacy 0.05/tag profile boost could
        # dominate current requirements. Keep the profile a weak prior here.
        self.scorer = HybridContextualReranker(llm_ranker=None, top_n=50, profile_weight=0.001) if mode == "hybrid" else LockedWeightedRrfTop10Reranker()

    def rerank(self, retrieval, *, top_k):
        ranked = ()
        if retrieval.candidates:
            view = ScoringInput(retrieval.candidate_set_id, retrieval.legacy_requirements, retrieval.candidates, retrieval.session_id, retrieval.turn)
            if self.mode == "hybrid":
                self.scorer.set_session_context(view.session_id, retrieval.state_snapshot["profile_hints"],
                    requirements=view.requirements, asked_attributes=[q["ask_attribute"] for q in retrieval.state_snapshot["asked_questions"] or ()])
            base = self.scorer.rerank(view, top_k=len(view.candidates))
            state = retrieval.state_snapshot
            preferences = [(p["value"], p["weight"]) for name, prefs in state["soft_preferences"].items()
                           if name not in {'shopping_purpose', 'shopping_occasion',
                                           'budget_floor_target', 'fit_avoid', 'size'} for p in prefs]
            preferences += [(tag, 0.15) for tag in state["profile_hints"].get("preference_tags", [])]
            avoid = [str(p['value']) for p in state['soft_preferences'].get('fit_avoid', ())]
            sizes = [str(p['value']) for p in state['soft_preferences'].get('size', ())]
            products = {c.parent_asin: c.product for c in retrieval.candidates}
            rescored = []
            for row in base.ranked_candidates:
                text_terms = terms(" ".join(_text(v) for v in products[row.parent_asin].values()))
                boost = sum(weight * len(terms(value) & text_terms) / max(1, len(terms(value)))
                            for value, weight in preferences) * 0.001
                penalty = 0.001 if any(style_matches(products[row.parent_asin], value)
                                       for value in avoid) else 0.0
                size_boost = 0.001 if any(size_matches(products[row.parent_asin], value)
                                          for value in sizes) else 0.0
                rescored.append(replace(row, score=row.score + boost + size_boost - penalty,
                    evidence=(*row.evidence, f"context_boost:{boost:.6f}",
                              f"soft_size_boost:{size_boost:.6f}",
                              f"soft_fit_avoid_penalty:{penalty:.6f}")))
            rescored.sort(key=lambda row: (-row.score, row.rank))
            ranked = tuple(replace(row, rank=i) for i, row in enumerate(rescored[:top_k], 1))
        result = RankingResultV2(retrieval.candidate_set_id, retrieval.session_id, retrieval.turn,
                                 retrieval.state_version, ranked, self.mode + "+weighted_soft_context_cpu",
                                 score_semantics="rank_derived_not_probability" if self.mode == "hybrid" else "uncalibrated")
        result.validate_against(retrieval, top_k=top_k)
        return result
