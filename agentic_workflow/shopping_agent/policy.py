"""Bounded pre/post retrieval decisions with actual-question accounting."""
from dataclasses import asdict, dataclass


def conversational_choice_question(attribute, options):
    """Render only the catalog-supported options chosen by the planner."""
    choices = (f'{options[0]} or {options[1]}' if len(options) == 2 else
               ', '.join(options[:-1]) + f', or {options[-1]}')
    if attribute == 'color':
        lead = f'I found {choices} among the listed colors. Is one closer to what you want?'
    elif attribute == 'material':
        lead = f'I found {choices} in the material descriptions. Do you have a fabric preference?'
    elif attribute == 'style':
        lead = f'These listings describe {choices} styles. Do you lean one way?'
    else:
        lead = f'Some listings mention {choices}. Is one closer to how you plan to use it?'
    return lead + ' I can keep them all in the mix; you can also say "show me first".'


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


def empty_pool_message(state):
    """Offer a reversible next move without guessing which filter caused zero results."""
    category = state.hard_constraints.get('subtype') or state.hard_constraints.get('category')
    label = 'T-shirt' if category == 't-shirt' else str(category).replace('-', ' ') if category else 'product'
    adjustable = next((slot.replace('_', ' ') for slot in
                       ('color', 'material', 'size', 'brand', 'style')
                       if slot in state.hard_constraints), None)
    lead = f'I cannot verify a match for your {label} search in this catalog. I have not loosened your requirements to force a result.'
    if adjustable:
        return lead + f' Would you like to revisit the {adjustable}, or try another product type?'
    return lead + ' Would you like to try another product type?'


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
        alternatives = state.suggestions.get('category_alternatives') or ()
        if self.max_questions is None and len(alternatives) >= 2:
            plurals = {'t-shirt': 'T-shirts', 'dress': 'dresses', 'jacket': 'jackets',
                       'bag': 'bags', 'shoes': 'shoes', 'shirt': 'shirts',
                       'pants': 'pants', 'shorts': 'shorts', 'skirt': 'skirts',
                       'jersey': 'jerseys', 'earrings': 'earrings',
                       'necklace': 'necklaces', 'ring': 'rings', 'bracelet': 'bracelets'}
            names = [plurals.get(value, value + 's') for value in alternatives]
            choices = ' or '.join(names) if len(names) == 2 else ', '.join(names[:-1]) + ', or ' + names[-1]
            cap = state.hard_constraints.get('price_max')
            kept = f' Your ${float(cap):g} limit is saved.' if cap is not None else ''
            scoped = state.suggestions.get('category_scoped_details') or {}
            detail_note = " I'll keep the details for each item separate." if any(scoped.values()) else ''
            question = clarify(state, 'category',
                f'Which would you like to explore first—{choices}? We can switch later.{kept}{detail_note}',
                'choose_category_alternative')
            question.question.update(reason='choose_category_alternative',
                                     options=list(alternatives),
                                     scoped_details=scoped,
                                     option_labels={value: 'T-shirt' if value == 't-shirt'
                                                    else value.replace('-', ' ').title()
                                                    for value in alternatives})
            return question
        conversation_act = state.suggestions.get('conversation_act')
        shared_edit = state.suggestions.get('deferred_item_edit') or {}
        if (conversation_act == 'deferred_item_edit' and shared_edit.get('shared_updates')
                and state.hard_constraints.get('category')):
            # Recheck the visible category when a shared price constraint changes.
            # Retaining its old page could present products that violate the new limit.
            return PolicyDecision('retrieve', 'deferred_item_shared_constraint')
        if self.max_questions is None and conversation_act:
            return PolicyDecision('acknowledge', 'conversation_' + conversation_act)
        if (self.max_questions is None and state.suggestions.get('question_focus')
                and state.hard_constraints.get('category') and state.shown_asins
                and not state.suggestions.get('requirements_changed')
                and not state.suggestions.get('requested_results')
                and not state.suggestions.get('negative_feedback')
                and not state.suggestions.get('requirement_conflicts')):
            focus = state.suggestions['question_focus']
            scope_start = state.suggestions.get('question_scope_start', 0)
            narrowing = [q for q in state.asked_questions or ()
                         if q.get('turn', 0) >= scope_start
                         and q.get('evidence', {}).get('expected_reduction') is not None]
            if any(q.get('target_slot') == focus for q in narrowing):
                return PolicyDecision('acknowledge', 'conversation_focus_already_covered')
            if state.suggestions.get('clarification_streak', 0) >= 2 or len(narrowing) >= 3:
                return PolicyDecision('acknowledge', 'conversation_focus_attention_limit')
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
                gift = next((str(item.value) for item in state.soft_preferences.get('shopping_purpose', ())
                             if str(item.value).startswith('gift')), None)
                if gift:
                    recipient = gift.removeprefix('gift for ')
                    for_name = (f' for your {recipient}' if recipient != gift else '')
                    occasion = state.soft_preferences.get('shopping_occasion', ())
                    occasion_name = str(occasion[0].value) if occasion else ''
                    budget = state.hard_constraints.get('price_max')
                    target = state.soft_preferences.get('budget_target', ())
                    limit = (f' under ${float(budget):g}' if budget is not None else
                             f' around ${float(target[0].value):g}' if target else '')
                    question = clarify(state, 'category',
                                       f'A {occasion_name + " " if occasion_name else ""}gift{for_name}{limit} sounds lovely. '
                                       'Would you like to start with a T-shirt, shoes, or a bag?',
                                       'missing_category')
                    question.question.update(gift_context=True,
                                             options=['t-shirt', 'shoes', 'bag'],
                                             option_labels={'t-shirt': 'T-shirt',
                                                            'shoes': 'Shoes', 'bag': 'Bag'})
                    return question
                question = clarify(state, 'category',
                    'What type of product would you like to explore? You can start with one and change direction later.',
                    'missing_category')
                question.question.update(options=['t-shirt', 'dress', 'shoes'],
                                         option_labels={'t-shirt': 'T-shirt',
                                                        'dress': 'Dress', 'shoes': 'Shoes'})
                return question
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
        if (not ranking.ranked_candidates and any(
                'budget_unverifiable:' in warning for warning in getattr(retrieval, 'warnings', ()))):
            already_asked = any(q.get('reason') == 'unverifiable_budget'
                                for q in state.asked_questions or ())
            if not already_asked or context.get('requirements_changed'):
                cap = state.hard_constraints.get('price_max')
                floor = state.hard_constraints.get('price_min')
                bound_text = (f'${float(floor):g}–${float(cap):g} range' if floor is not None and cap is not None
                              else f'${float(cap):g} cap' if cap is not None
                              else f'${float(floor):g} minimum')
                decision = clarify(state, 'budget',
                    f'I found possible products, but this catalog has no prices, so I cannot verify your {bound_text}. '
                    'Would you like to see unpriced ideas while keeping your price preference, or keep the strict limit?',
                    'unverifiable_budget')
                decision.question.update(reason='unverifiable_budget', budget_cap=cap,
                                         budget_floor=floor,
                                         budget_target_existing=bool(state.soft_preferences.get('budget_target')),
                                         budget_floor_target_existing=bool(state.soft_preferences.get('budget_floor_target')),
                                         options=['show unpriced ideas', 'keep strict limit'],
                                         option_labels={'show unpriced ideas': 'Show unpriced ideas',
                                                        'keep strict limit': 'Keep strict limit'})
                return decision
            return PolicyDecision('recommend', 'budget_unverifiable_repeat')
        # An explicit request to see results takes precedence over optional
        # facet exploration, even when the shopper named a question focus.
        if context.get('requested_results'):
            return PolicyDecision('recommend', 'user_requested_results')
        scope_start = context.get('question_scope_start', 0)
        narrowing_questions = [q for q in state.asked_questions or ()
                               if q.get('turn', 0) >= scope_start
                               and q.get('evidence', {}).get('expected_reduction') is not None]
        focus = context.get('question_focus')
        if focus:
            if not ranking.ranked_candidates:
                return clarify(state, 'other',
                    empty_pool_message(state),
                    'empty_eligible_pool')
            if context.get('clarification_stalled') or context.get('clarification_streak', 0) >= 2 or len(narrowing_questions) >= 3:
                return PolicyDecision('recommend', 'focus_attention_limit')
            if any(q.get('target_slot') == focus for q in narrowing_questions):
                return PolicyDecision('recommend', 'focus_already_covered')
            question = self._candidate_question(state, retrieval, focus=focus)
            return question or PolicyDecision('recommend', 'focus_not_distinguished')
        # Hard bounds on interrogation, not on the length of the conversation.
        if context.get('clarification_stalled'):
            return PolicyDecision('recommend', 'clarification_no_progress')
        if context.get('clarification_streak', 0) >= 2:
            return PolicyDecision('recommend', 'clarification_streak_limit')
        if (not context.get('requirements_changed') and not context.get('negative_feedback')
                and not context.get('decision_help')):
            return PolicyDecision('recommend', 'no_new_requirements')
        if not ranking.ranked_candidates:
            return clarify(state, 'other', empty_pool_message(state), 'empty_eligible_pool')
        if context.get('browse_first'):
            return PolicyDecision('recommend', 'browse_first')
        if getattr(state, 'intent', None) == 'browsing' and not context.get('decision_help'):
            return PolicyDecision('recommend', 'browsing_pace')
        if context.get('bypassed_optional_question'):
            return PolicyDecision('recommend', 'shopper_supplied_other_detail')
        # A results-only turn resets the consecutive counter, but must not
        # replenish the attention budget for this shopping target. Necessary
        # conflict/empty-result recovery stays separate from optional narrowing.
        if len(narrowing_questions) >= 3:
            return PolicyDecision('recommend', 'target_clarification_budget')
        if not can_ask(state, None):
            return PolicyDecision('recommend', 'question_budget_exhausted')
        # Estimate reduction over retrieved candidates, not the entire catalog.
        # A question earns a turn only if it is likely to remove enough options
        # to offset the interruption; that bar rises with prior optional asks.
        question = self._candidate_question(state, retrieval)
        if not question:
            return PolicyDecision('recommend', 'no_valuable_question')
        reduction = question.question['evidence']['expected_reduction']
        expected_removed = len(retrieval.candidates) * reduction
        attention_cost = 7 + 2 * len(narrowing_questions)
        return (question if expected_removed >= attention_cost else
                PolicyDecision('recommend', 'manageable_candidate_pool'))

    @staticmethod
    def _candidate_question(state, retrieval, *, focus=None):
        # A shopper-requested facet may be revisited even if previously asked.
        from mvp.shadow_policy import shadow_question_board
        known = set(state.hard_constraints) | set(state.soft_preferences) | set(state.exclusions)
        asked = {q.get('target_slot') for q in state.asked_questions or ()
                 if q.get('turn', 0) >= state.suggestions.get('question_scope_start', 0)}
        if focus:
            asked.discard(focus)
        board = shadow_question_board([dict(c.product) for c in retrieval.candidates],
            already_known=known,
            already_asked=asked | set(state.suggestions.get('cleared_slots', ())),
            turns_left=None)
        best = next((q for q in board if q['attribute'] in ({focus} if focus else
                     {'color', 'material', 'style', 'use_case'})
                     and q['coverage'] >= 0.6 and q['expected_reduction'] >= 0.3 and q['net_value'] > 0), None)
        if not best:
            return None
        options = [o['value'] for o in best['options']]
        decision = clarify(state, 'feature',
                           conversational_choice_question(best['attribute'], options),
                           'candidate_information_gain')
        decision.question.update(target_slot=best['attribute'],
            options=options, option_labels={value: value for value in options}, evidence=best)
        return PolicyDecision('recommend_and_clarify', decision.reason, decision.question)
