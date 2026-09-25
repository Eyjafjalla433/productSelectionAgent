"""Official reset/respond API and observable, versioned orchestration."""
from copy import deepcopy
from dataclasses import replace
import json
import re
from time import perf_counter

from intent_router.turn_router import TurnIntentRouter
from intent_router.catalog_lexicon import NON_BRAND_STORE_WORDS
from state_memory.structured_manager import StructuredStateMemoryManager
from .retrieval import RetrievalRequest, StateAwareRetriever
from .ranking import StateAwareReranker
from .policy import PreRetrievalPolicy, PostRetrievalPolicy, PolicyDecision


class FinalAgent:
    def __init__(self, catalog_path=None, *, retrieval_backend=None, ranking_mode=None, orchestration_mode="adaptive", clarification_mode=None, trace_enabled=False, requirement_enhancer=None, search_adapter=None, max_turns=10):
        if max_turns is not None and (type(max_turns) is not int or max_turns < 1):
            raise ValueError('max_turns must be a positive integer or None')
        self.max_turns = max_turns
        if orchestration_mode not in {"adaptive", "score_compat"}:
            raise ValueError("orchestration_mode must be adaptive or score_compat")
        if ranking_mode is None:
            ranking_mode = "locked" if orchestration_mode == "score_compat" else "hybrid"
        if clarification_mode is None:
            clarification_mode = "state_evidence" if orchestration_mode == "score_compat" else "strict_dynamic"
        profiles = {
            "conversational": (0, 0, None),
            "strict_dynamic": (0, 0, 2),
            "state_evidence": (4, 0, 2),
            "fixed_two_dynamic": (0, 2, 3),
            "one_then_value": (4, 1, 3),
        }
        if clarification_mode not in profiles:
            raise ValueError("unsupported clarification_mode")
        self.orchestration_mode = orchestration_mode
        self.clarification_mode = clarification_mode
        self.retriever = search_adapter if search_adapter is not None else StateAwareRetriever(catalog_path, backend=retrieval_backend,
                                             mode="recall_compat" if orchestration_mode == "score_compat" else "strict")
        brands = {str(p.get("store") or "").strip().lower() for p in self.retriever.products.values()}
        self.router = TurnIntentRouter(known_brands={b for b in brands if len(b) >= 3 and b not in NON_BRAND_STORE_WORDS})
        self.memory = StructuredStateMemoryManager(max_turns=max_turns)
        minimum_evidence, minimum_questions, max_questions = profiles[clarification_mode]
        self.pre_policy = PreRetrievalPolicy(minimum_evidence, minimum_questions, max_questions)
        self.reranker = search_adapter if search_adapter is not None else StateAwareReranker(ranking_mode)
        self.post_policy = PostRetrievalPolicy(max_questions)
        self.calls = {}
        self.result_history = {}
        self.trace_enabled = trace_enabled
        self.trace = []
        self.errors = []
        self.requirement_enhancer = requirement_enhancer

    @property
    def catalog_size(self):
        """Stable C-layer catalog capability; callers need not inspect A internals."""
        return len(self.retriever.products)

    def get_catalog_product(self, parent_asin):
        """Return a detached catalog record for presentation and handoff."""
        product = self.retriever.products.get(parent_asin)
        return deepcopy(product) if product is not None else None

    def get_trace(self, session_id, turn):
        """Return one detached C-layer trace without exposing shared-list order."""
        for row in reversed(self.trace):
            if row.get("session_id") == session_id and row.get("turn") == turn:
                return deepcopy(row)
        raise KeyError(f"No trace for session {session_id!r}, turn {turn!r}")

    def get_session_traces(self, session_id):
        """Return detached traces for a session in turn order."""
        rows = [row for row in self.trace if row.get("session_id") == session_id]
        return deepcopy(sorted(rows, key=lambda row: row.get("turn", 0)))

    def retain_previous_result_page(self, session_id, turn):
        """Keep Undo aligned with a page the runtime retained after exhausted More."""
        history = self.result_history.get(session_id, [])
        if (len(history) >= 2 and history[-1]['ranking'].turn == turn and
                history[-1]['key'] == history[-2]['key']):
            history.pop()
            return history[-1]['ranking'].turn
        return None

    def reset(self, session_id, user_profile):
        self.memory.reset(session_id, user_profile)
        self.calls[session_id] = {}
        self.result_history[session_id] = []

    def drop_session(self, session_id):
        """Release C-layer and state-memory data for an expired web session."""
        self.calls.pop(session_id, None)
        self.result_history.pop(session_id, None)
        self.memory.drop(session_id)
        # Trace and diagnostic errors are session data too. Keeping them after
        # TTL/LRU eviction would make the otherwise bounded web runtime grow
        # indefinitely and would retain user messages beyond the session.
        self.trace[:] = [row for row in self.trace if row.get("session_id") != session_id]
        self.errors[:] = [row for row in self.errors if row.get("session_id") != session_id]

    def record_product_feedback(self, session_id, *, source_turn, rejected_asins=(), liked_asins=()):
        if session_id not in self.calls:
            raise ValueError("reset(session_id, user_profile) is required")
        return self.memory.record_product_feedback(
            session_id,
            source_turn=source_turn,
            rejected_asins=rejected_asins,
            liked_asins=liked_asins,
        )

    def record_control(self, session_id, user_message, turn, response, control, top_k=10):
        """Record a C-layer conversational control without mutating requirements."""
        if session_id not in self.calls:
            raise ValueError("reset(session_id, user_profile) is required")
        if type(turn) is not int or turn < 1 or (self.max_turns is not None and turn > self.max_turns):
            raise ValueError("turn exceeds the configured conversation limit")
        if type(top_k) is not int or not 1 <= top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        calls = self.calls[session_id]
        if turn in calls:
            previous = calls[turn]
            if (user_message, top_k) != previous[:2]:
                raise ValueError("conflicting replay of the same turn")
            return deepcopy(previous[2])
        if turn != len(calls) + 1:
            raise ValueError("turns must be sequential")
        raw_usage = response.get("usage") or {}
        safe_response = {
            "message": str(response.get("message") or ""),
            "ask_attribute": None,
            "recommendations": [],
            "usage": {
                "prompt_tokens": max(0, int(raw_usage.get("prompt_tokens") or 0)),
                "completion_tokens": max(0, int(raw_usage.get("completion_tokens") or 0)),
            },
        }
        calls[turn] = (user_message, top_k, deepcopy(safe_response))
        if self.trace_enabled:
            self.trace.append(
                {
                    "session_id": session_id,
                    "turn": turn,
                    "user_message": user_message,
                    "events": [
                        {"stage": "0_control", "elapsed_ms": 0.0, "output": deepcopy(control)},
                        {"stage": "response", "elapsed_ms": 0.0, "output": deepcopy(safe_response)},
                    ],
                }
            )
        return deepcopy(safe_response)

    def dismiss_pending_question(self, session_id):
        """Drop a declined optional question without changing shopping constraints."""
        if session_id not in self.calls:
            raise ValueError("reset(session_id, user_profile) is required")
        self.memory.pending[session_id] = None

    def respond(self, session_id, user_message, turn, top_k=10, *, product_feedback=None):
        if session_id not in self.calls:
            raise ValueError("reset(session_id, user_profile) is required")
        if type(turn) is not int or turn < 1 or (self.max_turns is not None and turn > self.max_turns):
            raise ValueError("turn exceeds the configured conversation limit")
        if type(top_k) is not int or not 1 <= top_k <= 10:
            raise ValueError("top_k must be between 1 and 10")
        calls = self.calls[session_id]
        if turn in calls:
            previous = calls[turn]
            if (user_message, top_k) != previous[:2]:
                raise ValueError("conflicting replay of the same turn")
            return deepcopy(previous[2])
        if turn != len(calls) + 1:
            raise ValueError("turns must be sequential")
        started = perf_counter()
        events = []

        def observe(stage, payload):
            events.append({"stage": stage, "elapsed_ms": round((perf_counter() - started) * 1000, 3), "output": payload})

        state = None
        retrieval = None
        decision = None
        stage = "1_intent"
        model_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        try:
            if product_feedback:
                stage = "0_product_feedback"
                feedback = self.record_product_feedback(
                    session_id, source_turn=turn,
                    rejected_asins=product_feedback.get('rejected_asins', ()),
                    liked_asins=product_feedback.get('liked_asins', ()),
                )
                observe("0_product_feedback", feedback.to_dict())
            active_category_slot = self.memory.sessions[session_id].hard_slots.get('category')
            intent_result = self.router.understand_turn(
                user_message, pending_question=self.memory.pending[session_id],
                deferred_category_details=self.memory.sessions[session_id].deferred_category_details,
                active_category=active_category_slot.value if active_category_slot else None)
            uncertainty_only = intent_result.decision_evidence.get('uncertain_preference') and not intent_result.slot_updates
            if self.requirement_enhancer is not None and intent_result.decision_evidence.get('requirement_control') not in {'undo', 'redo'} and not intent_result.decision_evidence.get('search_reset') and not intent_result.decision_evidence.get('conversation_act') and not intent_result.decision_evidence.get('declined_options') and not uncertainty_only and not intent_result.decision_evidence.get('retry_search'):
                stage = "1_model_enhancement"
                enhancement = self.requirement_enhancer.enhance(user_message, intent_result.slot_updates or ())
                combined = tuple(intent_result.slot_updates or ()) + enhancement.updates
                intent_result = replace(
                    intent_result,
                    slot_updates=combined,
                    decision_evidence={
                        **intent_result.decision_evidence,
                        "model_assist": {
                            "provider": enhancement.provider,
                            "model": enhancement.model,
                            "accepted_updates": len(enhancement.updates),
                            "warning": enhancement.warning,
                        },
                    },
                )
                model_usage = dict(enhancement.usage)
                observe("1_model_enhancement", enhancement.to_dict())
                stage = "1_intent"
            handoff = intent_result.to_state_handoff(session_id=session_id, turn=turn)
            observe("1_intent", handoff)  # BP1: intent_result, handoff
            stage = "3_state"
            state = self.memory.update_from_intent(handoff)
            observe("3_state", state.to_dict())  # BP3: accumulated state
            stage = "4A_pre_policy"
            result_key = json.dumps([state.hard_constraints,
                {k: [p.value for p in v] for k, v in state.soft_preferences.items()},
                {k: (row.get('priority', 1.0), row.get('priority_turn'))
                 for k, row in state.slot_metadata.get('soft', {}).items()},
                state.exclusions], sort_keys=True, ensure_ascii=False)
            restored = None
            history_action = 'redo' if state.suggestions.get('requirement_redo') else 'undo'
            if self.orchestration_mode == 'adaptive' and state.suggestions.get('requirement_' + history_action) == 'applied':
                cached = next((item for item in reversed(self.result_history[session_id]) if item['key'] == result_key), None)
                if cached and all(self.retriever.products.get(key) == source for key, source in cached['sources'].items()):
                    rows = [row for row in cached['ranking'].ranked_candidates
                            if row.parent_asin not in state.rejected_asins
                            and StateAwareRetriever.satisfies(self.retriever, row.parent_asin, state)]
                    if rows:
                        restored = replace(cached['ranking'], turn=turn, state_version=state.state_version,
                            ranking_method='restored_previous_results',
                            ranked_candidates=tuple(replace(row, rank=i) for i, row in enumerate(rows[:top_k], 1)))
            pre_decision = PolicyDecision('restore', history_action + '_previous_results') if restored else self.pre_policy.decide(state)
            observe("4A_pre_policy", pre_decision.to_dict())  # BP4A
            if restored:
                ranking = restored
                decision = PolicyDecision('recommend', history_action + '_previous_results')
                observe('5_restore_results', {'source_turn': cached['ranking'].turn, 'candidate_set_id': ranking.candidate_set_id})
                observe('4B_ranking', ranking.to_dict())
                observe('4B_post_policy', decision.to_dict())
            elif pre_decision.action in {"clarify", "acknowledge"}:
                decision = pre_decision
                ranking = None
            else:
                stage = "2_retrieval"
                request = RetrievalRequest.from_state(state)
                observe("5_plan", request.to_dict())  # BP5: adaptive plan
                retrieval = self.retriever.generate(request)
                retrieval.validate_context(session_id=session_id, turn=turn, state_version=state.state_version)
                observe("2_retrieval", retrieval.to_dict())  # BP2: candidates + filter counts
                if not retrieval.candidates and any(
                    name not in {'shopping_purpose', 'shopping_occasion', 'budget_floor_target',
                                 'fit_avoid'}
                    for name in state.soft_preferences):
                    request = request.retry()  # Bounded re-orchestration; never relax hard constraints.
                    observe("5_retry_plan", request.to_dict())
                    retrieval = self.retriever.generate(request)
                    retrieval.validate_context(session_id=session_id, turn=turn, state_version=state.state_version)
                    observe("2_retrieval_retry", retrieval.to_dict())
                stage = "4B_ranking"
                ranking = self.reranker.rerank(retrieval, top_k=top_k)
                ranking.validate_against(retrieval, top_k=top_k)
                observe("4B_ranking", ranking.to_dict())  # BP4B-rank
                stage = "4B_post_policy"
                decision = self.post_policy.decide(state, retrieval, ranking)
                observe("4B_post_policy", decision.to_dict())  # BP4B-policy
            recommendations = [] if decision.action == "clarify" or ranking is None else [
                {"parent_asin": row.parent_asin, "score": row.score} for row in ranking.ranked_candidates]
            response = {
                "message": decision.question["message"] if decision.question else ("Here are ranked catalog candidates. Please check their product details." if recommendations else "No candidates passed the current catalog checks."),
                "ask_attribute": decision.question["ask_attribute"] if decision.question else None,
                "recommendations": recommendations,
                "usage": model_usage,
            }
            if decision.reason == 'awaiting_product_context':
                response['message'] = "No rush. When you're ready, tell me what you're shopping for and I'll take it from there."
            if decision.reason == 'conversation_acknowledgement':
                response['message'] = "You're welcome. We can pick up here whenever you're ready."
            elif decision.reason == 'conversation_pause':
                response['message'] = "Take your time. I'll keep your preferences here."
            elif decision.reason == 'conversation_uncertainty':
                response['message'] = "No need to decide yet. I'll keep your current preferences. You can ask for more options whenever you like."
            elif decision.reason == 'conversation_declined_options':
                response['message'] = (
                    "That's fine—we can leave those choices open. I've kept your current options, and you can narrow them later."
                    if state.suggestions.get('accepted_options') else
                    "No need to pick between those. I'll keep your list and preferences as they are. "
                    "If neither works for you, tell me what you'd rather see, or ask for more options.")
            elif decision.reason == 'conversation_unmatched_option':
                response['message'] = ("I couldn't connect that to the options I offered, so I've kept your list "
                                       "unchanged. Tell me what matters to you, or ask for more options.")
            elif decision.reason == 'conversation_focus_repeat':
                focus = state.suggestions.get('question_focus')
                label = {'material': 'fabric', 'style': 'fit', 'use_case': 'intended use'}.get(focus, focus or 'that detail')
                pending = state.pending_question or {}
                response['message'] = (
                    f"I hear you—{label} matters. I've kept the current results and left that preference open. "
                    + ("You can choose one of the listed options, or we can compare what is already here."
                       if pending.get('target_slot') == focus and pending.get('options') else
                       "These listings still don't give me a reliable split to ask about. "
                       "You can name a specific preference, or compare the options as they are.")
                )
            elif decision.reason in {'conversation_focus_attention_limit', 'conversation_focus_already_covered'}:
                focus = state.suggestions.get('question_focus')
                label = {'material': 'fabric', 'style': 'fit', 'use_case': 'intended use'}.get(
                    focus, focus or 'that detail')
                if decision.reason == 'conversation_focus_already_covered':
                    response['message'] = (f"We looked at the listed {label} choices earlier, so I won't ask "
                                           "the same question again. I've kept your current results. "
                                           "You can name what you prefer or compare these options as they are.")
                else:
                    response['message'] = (f"I hear that {label} matters. I've asked enough narrowing questions "
                                           "for now, so I've kept your current results. You can name a "
                                           "preference directly or ask me to compare them.")
            elif decision.reason == 'conversation_show_current_results':
                response['message'] = (
                    "Of course—here are the same options. We can leave that detail undecided "
                    "while you compare them; tell me another preference whenever you're ready."
                )
            elif decision.reason == 'conversation_browse_again':
                response['message'] = (
                    "Of course—no pressure. I'll keep these options here; we can narrow or compare them "
                    "whenever you're ready." if state.shown_asins else
                    "Of course—browse at your own pace. Tell me what kind of product you'd like to explore."
                )
            elif decision.reason == 'conversation_unsure_category':
                purpose = state.soft_preferences.get('shopping_purpose', ())
                if purpose and str(purpose[0].value).startswith('gift for '):
                    recipient = str(purpose[0].value).removeprefix('gift for ')
                    occasion = state.soft_preferences.get('shopping_occasion', ())
                    kind = f'{occasion[0].value} gift' if occasion else 'gift'
                    response['message'] = (f"That's okay — I've kept the {kind} for your {recipient} in mind. "
                                           "This catalog needs a product type to start; we could try a T-shirt, shoes, or a bag. "
                                           "You can change direction at any time.")
                else:
                    response['message'] = ("No rush — we can start with a T-shirt, dress, or shoes. "
                                           "Choose one below or name another product type; we can change it later.")
            elif decision.reason == 'conversation_category_both':
                options = (state.pending_question or {}).get('options', ())
                names = ' or '.join(str(value).replace('-', ' ') for value in options)
                response['message'] = ("I can explore those product types one at a time here. "
                                       f"Which should we start with—{names}? Your other requirements are saved, "
                                       "and we can switch afterward.")
            elif decision.reason == 'conversation_deferred_item_edit':
                edit = state.suggestions.get('deferred_item_edit') or {}
                category = edit.get('category', 'item')
                saved = state.suggestions.get('deferred_category_details', {}).get(category, {})
                label = 'jeans' if category == 'pants' and saved.get('subtype', {}).get('values') == ('jeans',) else category
                changes = []
                for slot in edit.get('remove', ()):
                    excluded = edit.get('details', {}).get(slot + '_exclude', {}).get('values', ())
                    changes.append((f'any {slot} except {" or ".join(map(str, excluded))}'
                                    if excluded else f'no {slot} restriction'))
                for slot, detail in edit.get('details', {}).items():
                    if slot.endswith('_exclude'):
                        continue
                    new = ' or '.join(map(str, detail['values']))
                    old = ' or '.join(map(str, edit.get('previous', {}).get(slot, ())))
                    if slot in {'price_min', 'price_max'}:
                        direction = 'minimum' if slot == 'price_min' else 'limit'
                        changes.append(f'price {direction} to ${float(detail["values"][0]):g}')
                    elif detail.get('constraint_type') == 'soft' and slot == 'color':
                        options = tuple(map(str, detail['values']))
                        evidence = str(detail.get('evidence', '')).casefold()
                        preferred = next((value for value in options
                                          if re.search(r'\bprefer\s+' + re.escape(value.casefold()) + r'\b',
                                                       evidence)), None)
                        if preferred and len(options) == 2 and re.search(r'\b(?:okay|fine|acceptable)\b', evidence):
                            fallback = next(value for value in options if value != preferred)
                            changes.append(f'{preferred} first, with {fallback} also okay')
                        else:
                            changes.append(f'{new} as a preference, not a requirement')
                    else:
                        changes.append(f'{slot} from {old} to {new}' if old else f'{slot} as {new}')
                current_type = state.hard_constraints.get('category')
                next_step = (f"Your current {current_type} search stays put. You can switch to {label} "
                             "later or undo this edit." if current_type else
                             "I haven't switched the current search. You can choose which item to "
                             "explore first, or undo this edit.")
                response['message'] = (f"Got it—I updated the {label}: {', '.join(changes)}. "
                                       + next_step)
                if 'price_max' in edit.get('shared_updates', ()):
                    response['message'] += (f" Your ${float(state.hard_constraints['price_max']):g} limit"
                                            " is saved for both items.")
            elif decision.reason == 'conversation_history_noop':
                response['message'] = "Your current options and preferences are unchanged."
            elif decision.reason == 'conversation_budget_strict':
                response['message'] = ("Understood. I'll keep your price limit strict and won't label unpriced items as matches. "
                                       "If you want ideas without price verification later, say 'show unpriced ideas'.")
            elif decision.reason == 'budget_unverifiable_repeat':
                response['message'] = ("I still cannot verify your price limit from this catalog because it has no prices. "
                                       "You can keep the strict limit or ask to see unpriced ideas as a preview.")
            if recommendations and not decision.question and self.orchestration_mode == 'adaptive':
                hard = state.hard_constraints
                color = hard.get('color', '')
                if isinstance(color, (tuple, list)):
                    color = ' or '.join(map(str, color))
                preferred_color = state.soft_preferences.get('color', ())
                description = ' '.join(str(v) for v in (color, hard.get('subtype') or hard.get('category', '')) if v)
                if not color and preferred_color:
                    description += f', preferably {preferred_color[0].value}'
                    if (len(preferred_color) == 2 and
                            preferred_color[0].weight > preferred_color[1].weight):
                        description += f', with {preferred_color[1].value} also okay'
                target = state.soft_preferences.get('budget_target', ())
                floor_target = state.soft_preferences.get('budget_floor_target', ())
                if floor_target and target:
                    budget = (f', with a preferred ${float(floor_target[0].value):g}'
                              f'–${float(target[0].value):g} range')
                elif floor_target:
                    budget = f', preferably above ${float(floor_target[0].value):g}'
                else:
                    budget = f', aiming for around ${float(target[0].value):g}' if target else ''
                if state.suggestions.get('negative_feedback'):
                    fresh = sum(row['parent_asin'] not in state.shown_asins
                                for row in recommendations)
                    response['message'] = (
                        f"I hear you—those didn't work. I found {fresh} {'option' if fresh == 1 else 'options'} you haven't seen yet "
                        f"for {description}{budget}. Let's see whether these feel closer."
                        if fresh else
                        "I hear you—those didn't work. This search hasn't turned up a fresh verified option; "
                        "tell me what felt off and I'll adjust."
                    )
                elif (state.suggestions.get('requested_more') and
                      all(row['parent_asin'] in state.shown_asins for row in recommendations)):
                    response['message'] = (
                        "I couldn't find any new verified options under the current search. "
                        + ("I've kept the earlier match visible so we can look at its details "
                           if len(recommendations) == 1 else
                           "I've kept these earlier matches visible so we can compare them ") +
                        "or change a detail that matters to you."
                    )
                else:
                    response['message'] = (
                        f"Got it — {description}{budget}. I found one option to look at. "
                        "We can check its details or adjust your preferences."
                        if len(recommendations) == 1 else
                        f"Got it — {description}{budget}. I've pulled together {len(recommendations)} options so you can compare their details.")
                if decision.reason == 'browse_first':
                    response['message'] = ((f"Here's one {description} option to look at. "
                                           "No need to narrow things down yet—ask about its details "
                                           "or tell me what you'd change.") if len(recommendations) == 1 else
                                          (f"Here are {len(recommendations)} {description} options to browse. "
                                           "No need to narrow things down yet—tell me what catches your eye, "
                                           "or ask me to compare a couple."))
                bypassed = state.suggestions.get('bypassed_optional_question')
                if bypassed:
                    response['message'] += f" We can leave {bypassed.replace('_', ' ')} open for now."
                tentative_size = next((update.values for update in intent_result.slot_updates
                                       if update.slot == 'size' and update.operation == 'set'
                                       and update.constraint_type == 'soft'), None)
                if tentative_size:
                    shown_sizes = ' or '.join(str(value).upper() for value in tentative_size)
                    response['message'] += (f" I'll treat size {shown_sizes} as a preference, not a requirement "
                                            "until you confirm it; check the seller's size chart before buying.")
                purpose = state.soft_preferences.get('shopping_purpose', ())
                if purpose and str(purpose[0].value).startswith('gift'):
                    occasion = state.soft_preferences.get('shopping_occasion', ())
                    kind = f'{occasion[0].value} gift' if occasion else 'gift'
                    recipient = str(purpose[0].value).removeprefix('gift for ')
                    for_name = f' for your {recipient}' if recipient != 'gift' else ''
                    response['message'] += f" I'm keeping in mind that this is a {kind}{for_name}."
                if state.soft_preferences.get('fit_avoid'):
                    from .retrieval import style_matches
                    explicitly_baggy = sum(style_matches(
                        self.get_catalog_product(row['parent_asin']) or {}, 'baggy')
                        for row in recommendations)
                    if explicitly_baggy and explicitly_baggy < len(recommendations):
                        response['message'] += (" I moved listings explicitly described as baggy lower; "
                                                "I haven't ruled them out or assumed an unlabeled fit is right for you.")
                    elif explicitly_baggy:
                        response['message'] += (" These listings are all described as baggy, so I can't claim any "
                                                "meets your fit preference; I kept them available to inspect.")
                    else:
                        response['message'] += (" I'll keep your fit preference in mind, but these listings "
                                                "don't verify whether the fit is too baggy.")
                if decision.reason == 'focus_not_distinguished':
                    focus = state.suggestions.get('question_focus')
                    label = {'material': 'fabric', 'style': 'fit', 'use_case': 'intended use'}.get(
                        focus, focus)
                    response['message'] += (f" I hear that {label} matters to you. This batch doesn't give me "
                                            "a reliable choice to ask about, so I won't guess. "
                                            "You can name a preference or compare the listings as they are.")
                elif decision.reason in {'focus_attention_limit', 'focus_already_covered'}:
                    focus = state.suggestions.get('question_focus')
                    label = {'material': 'fabric', 'style': 'fit', 'use_case': 'intended use'}.get(
                        focus, focus or 'that detail')
                    if decision.reason == 'focus_already_covered':
                        response['message'] += (f" We looked at the listed {label} choices earlier, so I won't "
                                                "ask the same question again. You can name what you prefer "
                                                "or compare these options as they are.")
                    else:
                        response['message'] += (f" I hear that {label} matters. I've asked enough narrowing "
                                                "questions for now; the results are here to browse. "
                                                "You can name a preference directly or ask me to compare them.")
                if state.suggestions.get('declined_options'):
                    if state.suggestions.get('accepted_options'):
                        response['message'] = (
                            f"Those choices can stay open. I used your other detail to refresh {len(recommendations)} options."
                            if state.suggestions.get('requirements_changed') else
                            f"Those choices can stay open. Here are {len(recommendations)} options to consider.")
                    else:
                        response['message'] = (
                            f"Okay, I won't favor either of those choices. I used the other details you gave me to refresh "
                            f"{len(recommendations)} options."
                            if state.suggestions.get('requirements_changed') else
                            f"Okay, I won't favor either choice. Here are {len(recommendations)} options to consider.")
                if (state.suggestions.get('decision_help') and not state.suggestions.get('negative_feedback')):
                    response['message'] = (f"Let's choose from these {len(recommendations)} options together. "
                                           "I can compare #1 and #2, or you can tell me which detail matters most.")
                if (target or floor_target or 'price_max' in hard or 'price_min' in hard) and getattr(self.retriever, 'mode', '') == 'search_tool':
                    response['message'] += " This source doesn't include prices, so I can't confirm which fit your budget yet; I've kept it in your preferences."
            if (recommendations and decision.question and
                    getattr(self.retriever, 'mode', '') == 'search_tool'):
                target = state.soft_preferences.get('budget_target', ())
                floor = state.soft_preferences.get('budget_floor_target', ())
                if target or floor or any(key in state.hard_constraints
                                           for key in ('price_min', 'price_max')):
                    budget_phrase = (f"around ${float(target[0].value):g}" if target else
                                     f"above ${float(floor[0].value):g}" if floor else
                                     'within your price range')
                    response['message'] = (
                        "One quick note: this catalog has no prices, so I can't check which "
                        f"listings are {budget_phrase}. " + response['message']
                    )
            if (state.suggestions.get('decision_help') and decision.question
                    and decision.reason == 'candidate_information_gain'):
                response['message'] = "Happy to help you narrow it down. " + response['message']
            if state.suggestions.get('negative_feedback') and (decision.question or not recommendations):
                fresh = sum(row['parent_asin'] not in state.shown_asins
                            for row in recommendations)
                response['message'] = (
                    (f"I hear you—those didn't work. I found {fresh} options you haven't seen yet. "
                     if fresh else
                     "I hear you—those didn't work. This search hasn't turned up a fresh verified option; "
                     "tell me what felt off and I'll adjust. ")
                    + response['message']
                )
            edit = state.suggestions.get('deferred_item_edit') or {}
            if edit.get('shared_updates') and decision.reason != 'conversation_deferred_item_edit':
                category = edit.get('category', 'item')
                saved = state.suggestions.get('deferred_category_details', {}).get(category, {})
                label = ('jeans' if category == 'pants' and
                         saved.get('subtype', {}).get('values') == ('jeans',) else category)
                changes = ', '.join(f"{slot} {' or '.join(map(str, detail['values']))}"
                                    for slot, detail in edit.get('details', {}).items())
                response['message'] = (f"I've saved {changes} for the {label}; this search still covers "
                                       f"{state.hard_constraints.get('category')}. " + response['message'])
            undo_status = state.suggestions.get('requirement_undo')
            if restored:
                response['message'] = f"I've restored {len(recommendations)} previously shown options in their earlier order so you can pick up your comparison."
            if undo_status:
                prefix = "I've undone your last requirement change. " if undo_status == 'applied' else "There isn't an earlier requirement change to undo. "
                response['message'] = prefix + response['message']
            if state.suggestions.get('requirement_redo'):
                prefix = "I've restored your last undone requirement change. " if state.suggestions['requirement_redo'] == 'applied' else "There isn't an undone requirement change to restore. "
                response['message'] = prefix + response['message']
            if recommendations and self.orchestration_mode == 'adaptive':
                history = self.result_history[session_id]
                if not state.suggestions.get('requested_more'):
                    history[:] = [item for item in history if item['key'] != result_key]
                history.append({'key': result_key, 'ranking': deepcopy(ranking),
                                'sources': {row['parent_asin']: self.get_catalog_product(row['parent_asin']) for row in recommendations}})
                del history[:-24]  # Bounded per-session display history, not a product database.
        except Exception as exc:
            error = {"session_id": session_id, "turn": turn, "stage": stage, "type": type(exc).__name__, "message": str(exc)}
            self.errors.append(error)
            observe("error", error)
            # Fail closed: do not reuse stale candidates or drop hard constraints.
            decision = None
            message = ("I could not complete this search. Your requirements are saved; say 'try again' to retry."
                       if state is not None else "I could not process that request. Please send it again.")
            response = {"message": message, "ask_attribute": None, "recommendations": [], "usage": model_usage}
        observe("response", response)  # BP-response
        if state is not None:
            feedback = self.memory.record_execution(session_id, turn=turn,
                question=decision.question if decision else None,
                shown_asins=[row["parent_asin"] for row in response["recommendations"]],
                candidate_count=retrieval.returned_count if retrieval is not None else None)
            observe("3_feedback", feedback.to_dict())  # BP-feedback: count actual questions/shown ASINs
        calls[turn] = (user_message, top_k, deepcopy(response))
        if self.trace_enabled:
            self.trace.append({"session_id": session_id, "turn": turn, "user_message": user_message, "events": events})
        return response
