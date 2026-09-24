"""Official reset/respond API and observable, versioned orchestration."""
from copy import deepcopy
from dataclasses import replace
import json
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

    def respond(self, session_id, user_message, turn, top_k=10):
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
            intent_result = self.router.understand_turn(user_message, pending_question=self.memory.pending[session_id])
            uncertainty_only = intent_result.decision_evidence.get('uncertain_preference') and not intent_result.slot_updates
            if self.requirement_enhancer is not None and intent_result.decision_evidence.get('requirement_control') not in {'undo', 'redo'} and not intent_result.decision_evidence.get('conversation_act') and not uncertainty_only and not intent_result.decision_evidence.get('retry_search'):
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
                if not retrieval.candidates and state.soft_preferences:
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
            elif decision.reason == 'conversation_history_noop':
                response['message'] = "Your current options and preferences are unchanged."
            if recommendations and not decision.question and self.orchestration_mode == 'adaptive':
                hard = state.hard_constraints
                color = hard.get('color', '')
                if isinstance(color, (tuple, list)):
                    color = ' or '.join(map(str, color))
                preferred_color = state.soft_preferences.get('color', ())
                description = ' '.join(str(v) for v in (color, hard.get('category', '')) if v)
                if not color and preferred_color:
                    description += f', preferably {preferred_color[0].value}'
                target = state.soft_preferences.get('budget_target', ())
                budget = f', aiming for around ${float(target[0].value):g}' if target else ''
                response['message'] = f"Got it — {description}{budget}. I've pulled together {len(recommendations)} options so you can compare their details."
                if (target or 'price_max' in hard or 'price_min' in hard) and getattr(self.retriever, 'mode', '') == 'search_tool':
                    response['message'] += " This source doesn't include prices, so I can't confirm which fit your budget yet; I've kept it in your preferences."
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
