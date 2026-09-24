"""Bounded pre/post retrieval decisions with actual-question accounting."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class PolicyDecision:
    action: str
    reason: str
    question: dict | None = None

    def to_dict(self):
        return asdict(self)


def can_ask(state, max_questions=2):
    return state.turn < 10 and state.suggestions.get("clarification_count", 0) < max_questions


def clarify(state, attribute, message, reason, *, constraint_type=None, hard_value_limit=None):
    tier = constraint_type or ("hard" if attribute in {"category", "budget"} else "soft")
    return PolicyDecision("clarify", reason, {
        "question_id": f"{state.session_id}:{state.turn}:{attribute}",
        "turn": state.turn, "ask_attribute": attribute, "target_slot": attribute,
        "constraint_type": tier,
        **({"hard_value_limit": hard_value_limit} if hard_value_limit is not None else {}),
        "message": message,
    })


class PreRetrievalPolicy:
    def __init__(self, minimum_evidence=0, minimum_questions=0, max_questions=2):
        if type(minimum_evidence) is not int or minimum_evidence < 0:
            raise ValueError("minimum_evidence must be a nonnegative integer")
        if type(minimum_questions) is not int or type(max_questions) is not int or not 0 <= minimum_questions <= max_questions:
            raise ValueError("question limits must be integers with 0 <= minimum_questions <= max_questions")
        self.minimum_evidence = minimum_evidence
        self.minimum_questions = minimum_questions
        self.max_questions = max_questions

    def decide(self, state):
        if can_ask(state, self.max_questions):
            if not state.hard_constraints.get("category"):
                return clarify(state, "category", "What type of product are you looking for?", "missing_category")
            hard = state.hard_constraints
            if "price_min" in hard and "price_max" in hard and float(hard["price_min"]) > float(hard["price_max"]):
                return clarify(state, "budget", "Your minimum exceeds your maximum. What budget range should I use?", "contradictory_budget")
            evidence = len(set(hard) - {"category"}) + sum(len(values) for values in state.soft_preferences.values()) + sum(len(values) for values in state.exclusions.values())
            question_count = state.suggestions.get("clarification_count", 0)
            if question_count < self.minimum_questions or evidence < self.minimum_evidence:
                first_enrichment = state.suggestions.get("clarification_count", 0) == 0
                # Override-style openings carry an old trailing preference;
                # preserve the next disclosure as evidence before that value is
                # replaced. Ordinary buying/browsing enrichment remains soft
                # to protect recall for long catalog descriptions.
                override_style_opening = first_enrichment and bool(state.soft_preferences) and not (set(hard) - {"category"})
                hard_limit = 2 if override_style_opening else 0
                reason = "minimum_question_warmup" if question_count < self.minimum_questions else "insufficient_accumulated_evidence"
                return clarify(state, "other", "Please share any other requirements that matter.", reason,
                               constraint_type="hard" if hard_limit else "soft", hard_value_limit=hard_limit)
        return PolicyDecision("retrieve", "sufficient_context_or_question_limit")


class PostRetrievalPolicy:
    def __init__(self, max_questions=2):
        if type(max_questions) is not int or max_questions < 0:
            raise ValueError("max_questions must be a nonnegative integer")
        self.max_questions = max_questions

    def decide(self, state, retrieval, ranking):
        if not ranking.ranked_candidates:
            if can_ask(state, self.max_questions):
                attr = "budget" if any(k.startswith("price_") for k in state.hard_constraints) else "other"
                return clarify(state, attr, "I could not verify a match. Which requirement, if any, may I change?", "empty_eligible_pool")
            return PolicyDecision("recommend", "no_verified_matches_question_limit")
        informed = bool(state.soft_preferences or set(state.hard_constraints) - {"category"} or state.exclusions)
        asked = {q["ask_attribute"] for q in (state.asked_questions or ())}
        if can_ask(state, self.max_questions) and state.suggestions.get("negative_feedback"):
            attr = next((a for a in ("feature", "other") if a not in asked), None)
            if attr:
                return clarify(state, attr, "What additional requirement would make these options a better match?", "negative_feedback")
        if can_ask(state, self.max_questions) and ((not informed and (retrieval.stats.filtered_count or 0) > 100) or state.suggestions.get("negative_feedback")) and "feature" not in asked:
            return clarify(state, "feature", "What specific feature matters most to you?", "broad_pool_or_negative_feedback")
        return PolicyDecision("recommend", "ranked_eligible_candidates")
