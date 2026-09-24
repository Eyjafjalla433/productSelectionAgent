from __future__ import annotations

from .models import ContextSnapshot, Intent, NextAction, Route, SessionState, UserProfile


class ContextProgrammer:
    OVERLOAD_THRESHOLD = 300
    RETRIEVAL_BUDGET = 100
    MAX_CLARIFICATION_TURNS = 2
    FORCE_RETRIEVAL_TURN = 8
    CLARIFICATION_PROMPTS = {
        "category": "What type of item are you looking for: a dress, shoes, bag, or jewelry?",
        "occasion": "What occasion is this for: work, casual wear, a party, or a wedding?",
        "price_max": "What is your approximate maximum budget?",
        "gender": "Who is the item for?",
        "size": "What size do you need?",
        "color": "Which color would you prefer?",
    }
    CLARIFICATION_ORDER = tuple(CLARIFICATION_PROMPTS)

    def build(self, query: str, state: SessionState, profile: UserProfile, debug: dict) -> ContextSnapshot:
        route = Route.BROWSING_DENSE if state.intent == Intent.BROWSING else Route.BUYING_FILTER
        clarification_slot = self._clarification_slot(state)
        clarification = (
            self.CLARIFICATION_PROMPTS[clarification_slot]
            if clarification_slot is not None
            else None
        )
        if state.decision_stage == "conversion":
            action = NextAction.CONVERT
        elif state.decision_stage == "comparison":
            action = NextAction.COMPARE
        elif debug.get("category_overridden") or debug.get("intent_changed"):
            action = NextAction.REROUTE
        elif clarification:
            action = NextAction.ASK_CLARIFICATION
        elif route == Route.BROWSING_DENSE:
            action = NextAction.RETRIEVE_BROWSING
        else:
            action = NextAction.RETRIEVE_BUYING
        if action == NextAction.ASK_CLARIFICATION:
            self._record_clarification(state, clarification_slot)
        debug = {
            **debug,
            "clarification_count": state.clarification_count,
            "last_clarification_slot": state.last_clarification_slot,
            "forced_retrieval": state.turn_id >= self.FORCE_RETRIEVAL_TURN,
        }
        must = {name: slot.value for name, slot in state.hard_slots.items()}
        should = {
            name: {slot.value: self._weight(slot, state.turn_id)}
            for name, slot in state.soft_slots.items()
        }
        return ContextSnapshot(
            query=query,
            intent=state.intent,
            route=route,
            action=action,
            must_match=must,
            should_match=should,
            must_not_match=state.rejected_values.copy(),
            profile_hints=self._profile_hints(profile, must),
            clarification_question=clarification,
            retrieval_budget=self.RETRIEVAL_BUDGET,
            session_summary=state.summary,
            debug=debug,
        )

    def _clarification_slot(self, state: SessionState) -> str | None:
        if (
            state.turn_id >= self.FORCE_RETRIEVAL_TURN
            or state.clarification_count >= self.MAX_CLARIFICATION_TURNS
        ):
            return None
        overloaded = state.candidate_count is not None and state.candidate_count > self.OVERLOAD_THRESHOLD
        ambiguous_buying = state.intent in (Intent.UNKNOWN, Intent.BUYING) and "category" not in state.hard_slots
        if not overloaded and not ambiguous_buying:
            return None
        for slot in self.CLARIFICATION_ORDER:
            if slot not in state.hard_slots:
                return slot
        return None

    @staticmethod
    def _record_clarification(state: SessionState, slot: str | None) -> None:
        # ``build`` may run twice after one user message (before and after
        # retrieval feedback).  Count at most one question per dialogue turn.
        if state.last_clarification_turn == state.turn_id:
            return
        state.clarification_count += 1
        state.last_clarification_turn = state.turn_id
        state.last_clarification_slot = slot

    @staticmethod
    def _weight(slot, turn: int) -> float:
        age = turn - slot.source_turn
        return round(slot.priority * slot.confidence * (0.85 ** age), 3)

    @staticmethod
    def _profile_hints(profile: UserProfile, hard_slots: dict) -> dict[str, list]:
        return {name: values for name, values in profile.stable_preferences.items() if name not in hard_slots}
