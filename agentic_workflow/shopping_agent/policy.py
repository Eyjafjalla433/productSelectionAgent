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
    turn_limit = state.suggestions.get('max_turns', 10)
    return (turn_limit is None or state.turn < turn_limit) and (max_questions is None or state.suggestions.get("clarification_count", 0) < max_questions)


def clarify(state, attribute, message, reason, *, constraint_type=None, hard_value_limit=None):
    tier = constraint_type or ("hard" if attribute in {"category", "budget"} else "soft")
    return PolicyDecision("clarify", reason, {
        "question_id": f"{state.session_id}:{state.turn}:{attribute}",
        "turn": state.turn, "ask_attribute": attribute, "target_slot": attribute,
        "constraint_type": tier,
        **({"hard_value_limit": hard_value_limit} if hard_value_limit is not None else {}),
        "message": message,
    })


def missing_detail_question(state, attribute, reason):
    """Choose an unanswered dimension, not the next item in a turn script."""
    known = set(state.hard_constraints) | set(state.soft_preferences) | set(state.exclusions)
    asked = {q.get('target_slot') for q in state.asked_questions or ()}
    skipped = set(state.suggestions.get('cleared_slots', ()))
    options = (
        ('style', 'Do you prefer a relaxed fit or something more fitted?', '你更喜欢宽松一点，还是合身一点的款式？'),
        ('use_case', 'Will you mostly use it day to day, for work, or for an activity?', '主要是日常用、上班用，还是准备运动或出门时用呢？'),
        ('material', 'Is there a material you prefer or would rather avoid?', '材质上有没有你偏爱的，或者不想要的？'),
    )
    apparel = state.hard_constraints.get('category') in {'t-shirt', 'shirt', 'dress', 'jacket', 'pants', 'shorts', 'skirt', 'jersey'}
    for slot, english, chinese in options:
        if slot in known | asked | skipped or (slot == 'style' and not apparel):
            continue
        decision = clarify(state, attribute, english, reason)
        decision.question.update(target_slot=slot)
        return decision
    return clarify(state, attribute, 'What would you like to be different about these options?', reason)


class PreRetrievalPolicy:
    def __init__(self, minimum_evidence=0, minimum_questions=0, max_questions=2):
        if type(minimum_evidence) is not int or minimum_evidence < 0:
            raise ValueError("minimum_evidence must be a nonnegative integer")
        if type(minimum_questions) is not int or minimum_questions < 0 or (max_questions is not None and (type(max_questions) is not int or minimum_questions > max_questions)):
            raise ValueError("question limits must be integers with 0 <= minimum_questions <= max_questions")
        self.minimum_evidence = minimum_evidence
        self.minimum_questions = minimum_questions
        self.max_questions = max_questions

    def decide(self, state):
        if self.max_questions is None and state.suggestions.get('conversation_act'):
            return PolicyDecision('acknowledge', 'conversation_' + state.suggestions['conversation_act'])
        if self.max_questions is None and (state.suggestions.get('requested_results') or state.suggestions.get('clarification_stalled') or state.suggestions.get('clarification_streak', 0) >= 2):
            if not state.hard_constraints.get('category'):
                return PolicyDecision('acknowledge', 'awaiting_product_context')
            return PolicyDecision('retrieve', 'clarification_escape')
        if can_ask(state, self.max_questions):
            conflicts = state.suggestions.get('requirement_conflicts', ())
            if self.max_questions is None and conflicts:
                conflict = conflicts[0]
                old = ' / '.join(map(str, conflict['old']))
                new = ' / '.join(map(str, conflict['new']))
                from intent_router.router import COLOR_ALIASES, MATERIAL_ALIASES
                labels = {v: k for k, v in {**MATERIAL_ALIASES, **COLOR_ALIASES}.items()}
                old_zh = '、'.join(labels.get(v, str(v)) for v in conflict['old'])
                new_zh = '、'.join(labels.get(v, str(v)) for v in conflict['new'])
                question = clarify(state, 'feature', f'You previously wanted {old}. Replace it with {new}, or keep both? You can also keep the original or say "show me first".', 'confirm_requirement_change')
                question.question.update(target_slot=conflict['slot'], correction=conflict)
                return question
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
        if max_questions is not None and (type(max_questions) is not int or max_questions < 0):
            raise ValueError("max_questions must be a nonnegative integer")
        self.max_questions = max_questions

    def decide(self, state, retrieval, ranking):
        if self.max_questions is None:
            return self.conversational_decision(state, retrieval, ranking)
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
                return missing_detail_question(state, attr, "negative_feedback")
        if can_ask(state, self.max_questions) and ((not informed and (retrieval.stats.filtered_count or 0) > 100) or state.suggestions.get("negative_feedback")) and "feature" not in asked:
            return missing_detail_question(state, "feature", "broad_pool_or_negative_feedback")
        return PolicyDecision("recommend", "ranked_eligible_candidates")

    def conversational_decision(self, state, retrieval, ranking):
        context = state.suggestions
        # Hard bounds on interrogation, not on the length of the conversation.
        if context.get('requested_results'):
            return PolicyDecision('recommend', 'user_requested_results')
        if context.get('clarification_stalled'):
            return PolicyDecision('recommend', 'clarification_no_progress')
        if context.get('clarification_streak', 0) >= 2:
            return PolicyDecision('recommend', 'clarification_streak_limit')
        if not context.get('requirements_changed') and not context.get('negative_feedback'):
            return PolicyDecision('recommend', 'no_new_requirements')
        if not ranking.ranked_candidates:
            return clarify(state, 'other', 'I cannot verify a match with these requirements. Is there one you would like to change?', 'empty_eligible_pool')
        # A results-only turn resets the consecutive counter, but must not
        # replenish the attention budget for this shopping target. Necessary
        # conflict/empty-result recovery stays separate from optional narrowing.
        scope_start = context.get('question_scope_start', 0)
        narrowing_questions = [q for q in state.asked_questions or ()
                               if q.get('turn', 0) >= scope_start
                               and q.get('evidence', {}).get('expected_reduction') is not None]
        if len(narrowing_questions) >= 3:
            return PolicyDecision('recommend', 'target_clarification_budget')
        if len(retrieval.candidates) < 20 or not can_ask(state, None):
            return PolicyDecision('recommend', 'manageable_candidate_pool')
        # Reuse the evidence-only diagnostic: estimate reduction over retrieved
        # candidates, never claim to know the total catalogue match count.
        from mvp.shadow_policy import shadow_question_board
        known = set(state.hard_constraints) | set(state.soft_preferences) | set(state.exclusions)
        asked = {q.get('target_slot') for q in state.asked_questions or ()
                 if q.get('turn', 0) >= scope_start}
        board = shadow_question_board([dict(c.product) for c in retrieval.candidates],
            already_known=known, already_asked=asked | set(context.get('cleared_slots', ())), turns_left=None)
        best = next((q for q in board if q['attribute'] in {'color', 'material', 'style', 'use_case'}
                     and q['coverage'] >= 0.6 and q['expected_reduction'] >= 0.3 and q['net_value'] > 0), None)
        if not best:
            return PolicyDecision('recommend', 'no_valuable_question')
        options = [o['value'] for o in best['options']]
        choices = ' / '.join(options)
        from intent_router.option_labels import OPTION_LABELS_ZH, question_in_chinese
        decision = clarify(state, 'feature', f'These options differ in {best["attribute"]}: {choices}. Which do you prefer? You can also say "show me first".', 'candidate_information_gain')
        decision.question.update(target_slot=best['attribute'],
            options=options, option_labels={value: value for value in options}, evidence=best)
        return PolicyDecision('recommend_and_clarify', decision.reason, decision.question)
