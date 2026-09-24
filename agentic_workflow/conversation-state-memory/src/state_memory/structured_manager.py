"""State consumer for explicit intent operations. Never re-parses user text."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

from .contracts import StateSnapshotV2, WeightedPreference
from .models import ConstraintType, Intent, SessionState, Slot


def values(value):
    return value if isinstance(value, (list, tuple)) else (value,)


class StructuredStateMemoryManager:
    def __init__(self):
        self.sessions = {}
        self.profiles = {}
        self.versions = {}
        self.questions = {}
        self.pending = {}
        self.feedback_turns = {}
        self.context = {}

    def reset(self, session_id, user_profile):
        self.sessions[session_id] = SessionState(session_id)
        self.profiles[session_id] = deepcopy(user_profile)
        self.versions[session_id] = 0
        self.questions[session_id] = []
        self.pending[session_id] = None
        self.feedback_turns[session_id] = set()
        self.context[session_id] = {}

    def drop(self, session_id):
        """Release every state fragment owned by one completed/expired session."""
        for collection in (
            self.sessions, self.profiles, self.versions, self.questions,
            self.pending, self.feedback_turns, self.context,
        ):
            collection.pop(session_id, None)

    def update_from_intent(self, handoff):
        from intent_router.models import SlotUpdate

        if handoff.get("schema_version") != "2.0" or handoff.get("slot_updates") is None:
            raise ValueError("Explicit version-2 slot updates are required")
        sid, turn = handoff["session_id"], handoff["turn"]
        state = deepcopy(self.sessions[sid])
        if type(turn) is not int or not state.turn_id < turn <= 10:
            raise ValueError("State turn must advance within the ten-turn limit")
        operations = [SlotUpdate(**row) for row in handoff["slot_updates"]]
        incoming_category = next((u.values[0] for u in operations if u.slot == "category" and u.operation == "set"), None)
        old_category = state.hard_slots.get("category")
        category_changed = bool(old_category and incoming_category and old_category.value != incoming_category)
        if category_changed:
            # A new product target retains explicit monetary limits, not old
            # product-specific material, color, brand or feature requirements.
            state.hard_slots = {k: v for k, v in state.hard_slots.items() if k in {"price_min", "price_max"}}
            state.soft_slots.clear()
            state.rejected_values.clear()
            # Shown products belong to the previous shopping task. Clearing
            # them creates an intent-scoped novelty window for the new target.
            state.shown_asins.clear()
            state.rejected_asins.clear()
        incoming_intent = handoff["intent"]
        intent_changed = incoming_intent != "unknown" and incoming_intent != state.intent.value
        if incoming_intent != "unknown":
            state.intent = Intent(incoming_intent)
            state.intent_confidence = handoff["legacy_result"]["intent_confidence"]
        cleared = []
        for update in operations:
            name, op = update.slot, update.operation
            if op == "clear":
                if name == "preferences":
                    state.soft_slots.clear()
                elif name == "latest_preference":
                    if state.soft_slots:
                        latest = max(state.soft_slots, key=lambda key: (state.soft_slots[key].source_turn, list(state.soft_slots).index(key)))
                        state.soft_slots.pop(latest)
                elif name in {"feature", "other"}:
                    for slots in (state.hard_slots, state.soft_slots, state.rejected_values):
                        for key in list(slots):
                            if key == name or key.startswith("feature_"):
                                slots.pop(key)
                else:
                    state.hard_slots.pop(name, None)
                    state.soft_slots.pop(name, None)
                    state.rejected_values.pop(name, None)
                cleared.append(name)
            elif op == "set":
                target = state.hard_slots if update.constraint_type == "hard" else state.soft_slots
                other = state.soft_slots if update.constraint_type == "hard" else state.hard_slots
                # A weaker preference cannot erase an explicit hard requirement.
                if update.constraint_type == "soft" and name in state.hard_slots:
                    continue
                other.pop(name, None)
                value = update.values[0] if len(update.values) == 1 else update.values
                target[name] = Slot(value, turn, confidence=update.confidence if update.confidence is not None else 0.5,
                                    constraint_type=ConstraintType(update.constraint_type), evidence=update.evidence)
            elif op == "exclude":
                rejected = state.rejected_values.setdefault(name, [])
                for value in update.values:
                    if value not in rejected:
                        rejected.append(value)
                for slots in (state.hard_slots, state.soft_slots):
                    if name in slots:
                        remaining = [v for v in values(slots[name].value) if v not in rejected]
                        if not remaining:
                            slots.pop(name)
                        else:
                            slots[name].value = remaining[0] if len(remaining) == 1 else tuple(remaining)
            elif op == "remove_exclusion":
                remaining = [v for v in state.rejected_values.get(name, []) if v not in update.values]
                if remaining:
                    state.rejected_values[name] = remaining
                else:
                    state.rejected_values.pop(name, None)
        state.turn_id = turn
        state.summary = "; ".join([f"intent={state.intent.value}"] + [f"{k}={v.value}" for k, v in state.hard_slots.items()] + [f"prefer {k}={v.value}" for k, v in state.soft_slots.items()])
        self.sessions[sid] = state
        self.versions[sid] += 1
        self.pending[sid] = None
        self.context[sid] = {"query": handoff["legacy_result"]["raw_query"], "category_changed": category_changed,
                             "intent_changed": intent_changed, "cleared_slots": cleared,
                             "negative_feedback": handoff["legacy_result"].get("decision_evidence", {}).get("negative_feedback", False)}
        return self.snapshot(sid)

    def snapshot(self, sid):
        state = self.sessions[sid]
        metadata = {tier: {k: {**asdict(v), "constraint_type": v.constraint_type.value} for k, v in slots.items()}
                    for tier, slots in (("hard", state.hard_slots), ("soft", state.soft_slots))}
        return StateSnapshotV2(
            session_id=sid, turn=state.turn_id, state_version=self.versions[sid], intent=state.intent.value,
            intent_confidence=state.intent_confidence, query=self.context[sid].get("query", ""),
            hard_constraints={k: v.value for k, v in state.hard_slots.items()},
            soft_preferences={k: (WeightedPreference(v.value, round(v.priority * v.confidence * 0.85 ** (state.turn_id - v.source_turn), 6)),) for k, v in state.soft_slots.items()},
            exclusions=state.rejected_values, slot_metadata=metadata,
            profile_hints={"preference_tags": list(self.profiles[sid].get("preference_tags", []))},
            session_summary=state.summary, shown_asins=tuple(state.shown_asins),
            rejected_asins=tuple(state.rejected_asins),
            suggestions={**self.context[sid], "clarification_count": state.clarification_count},
            asked_questions=tuple(self.questions[sid]), pending_question=self.pending[sid],
        )

    def record_execution(self, sid, *, turn, question=None, shown_asins=(), candidate_count=None):
        state = self.sessions[sid]
        if turn != state.turn_id or turn in self.feedback_turns[sid]:
            raise ValueError("Feedback must be recorded once for the current turn")
        if question:
            question = deepcopy(question)
            self.questions[sid].append(question)
            self.pending[sid] = question
            state.clarification_count += 1
        state.shown_asins.extend(asin for asin in shown_asins if asin not in state.shown_asins)
        state.candidate_count = candidate_count
        self.feedback_turns[sid].add(turn)
        self.versions[sid] += 1
        return self.snapshot(sid)

    def record_product_feedback(self, sid, *, source_turn, rejected_asins=(), liked_asins=()):
        """Update explicit product-level feedback without advancing requirement turn."""
        state = self.sessions[sid]
        if type(source_turn) is not int or source_turn < 1:
            raise ValueError("source_turn must be a positive integer")
        shown = set(state.shown_asins)
        rejected = tuple(dict.fromkeys(str(asin) for asin in rejected_asins))
        liked = tuple(dict.fromkeys(str(asin) for asin in liked_asins))
        if any(not asin or asin not in shown for asin in (*rejected, *liked)):
            raise ValueError("Product feedback must reference a product shown in this session")
        for asin in liked:
            if asin in state.rejected_asins:
                state.rejected_asins.remove(asin)
        for asin in rejected:
            if asin not in state.rejected_asins:
                state.rejected_asins.append(asin)
        self.versions[sid] += 1
        self.context[sid] = {
            **self.context[sid],
            "product_feedback_turn": source_turn,
            "rejected_product_count": len(state.rejected_asins),
        }
        return self.snapshot(sid)
