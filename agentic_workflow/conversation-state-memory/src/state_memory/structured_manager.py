"""State consumer for explicit intent operations. Never re-parses user text."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import re

from .contracts import StateSnapshotV2, WeightedPreference
from .models import ConstraintType, Intent, SessionState, Slot


def values(value):
    return value if isinstance(value, (list, tuple)) else (value,)


def weighted_soft_values(name, slot, weight):
    """Keep an explicitly preferred catalog facet ahead of a fallback."""
    options = values(slot.value)
    text = slot.evidence.casefold()
    ordered = bool(name in {'color', 'material', 'style'} and len(options) == 2 and
                   re.search(r'\b(?:prefer|preferred|first choice|first|priority)\b', text) and
                   re.search(r'\b(?:okay|ok|fine|acceptable)\b', text) and
                   not re.search(r'\bnot\s+(?:okay|ok|fine|acceptable)\b', text))
    if not ordered:
        return (WeightedPreference(slot.value, weight),)
    preferred = []
    for option in options:
        label_pattern = re.escape(str(option).casefold())
        if any(re.search(pattern, text) for pattern in (
            rf'\bprefer(?:red)?\s+{label_pattern}\b',
            rf'\b{label_pattern}\s+(?:is\s+my\s+)?first(?:\s+choice)?\b',
            rf'\bfirst\s+choice\s+is\s+{label_pattern}\b',
            rf'\b(?:make|put)\s+{label_pattern}\s+(?:the\s+)?priority\b',
            rf'\b{label_pattern}\s+(?:is\s+)?(?:the\s+)?priority\b',
        )):
            preferred.append(option)
    if len(preferred) != 1:
        return (WeightedPreference(slot.value, weight),)
    fallback = next(option for option in options if option != preferred[0])
    return (WeightedPreference(preferred[0], weight),
            WeightedPreference(fallback, round(weight * 0.35, 6)))


class StructuredStateMemoryManager:
    def __init__(self, max_turns=10):
        self.max_turns = max_turns
        self.sessions = {}
        self.profiles = {}
        self.versions = {}
        self.questions = {}
        self.pending = {}
        self.feedback_turns = {}
        self.context = {}
        self.requirement_history = {}
        self.requirement_future = {}

    def reset(self, session_id, user_profile):
        self.sessions[session_id] = SessionState(session_id)
        self.profiles[session_id] = deepcopy(user_profile)
        self.versions[session_id] = 0
        self.questions[session_id] = []
        self.pending[session_id] = None
        self.feedback_turns[session_id] = set()
        self.context[session_id] = {}
        self.requirement_history[session_id] = []
        self.requirement_future[session_id] = []

    def drop(self, session_id):
        """Release every state fragment owned by one completed/expired session."""
        for collection in (
            self.sessions, self.profiles, self.versions, self.questions,
            self.pending, self.feedback_turns, self.context, self.requirement_history, self.requirement_future,
        ):
            collection.pop(session_id, None)

    def update_from_intent(self, handoff):
        from intent_router.models import SlotUpdate

        if handoff.get("schema_version") != "2.0" or handoff.get("slot_updates") is None:
            raise ValueError("Explicit version-2 slot updates are required")
        sid, turn = handoff["session_id"], handoff["turn"]
        state = deepcopy(self.sessions[sid])
        requirement_fields = ('hard_slots', 'soft_slots', 'rejected_values',
                              'deferred_category_details', 'shared_price_slots',
                              'active_item_price_category')
        before = {name: deepcopy(getattr(state, name)) for name in requirement_fields}
        product_before = {name: deepcopy(getattr(state, name))
                          for name in ('shown_asins', 'rejected_asins')}
        if type(turn) is not int or turn <= state.turn_id or (self.max_turns is not None and turn > self.max_turns):
            raise ValueError("State turn must advance within the configured conversation limit")
        operations = [SlotUpdate(**row) for row in handoff["slot_updates"]]
        turn_evidence = handoff['legacy_result'].get('decision_evidence', {})
        search_reset = bool(handoff['legacy_result'].get('decision_evidence', {}).get('search_reset'))
        search_reset_scope = handoff['legacy_result'].get('decision_evidence', {}).get('search_reset_scope')
        undo = handoff['legacy_result'].get('decision_evidence', {}).get('requirement_control') == 'undo'
        redo = handoff['legacy_result'].get('decision_evidence', {}).get('requirement_control') == 'redo'
        undo_applied = undo and bool(self.requirement_history[sid])
        redo_applied = redo and bool(self.requirement_future[sid])
        reset_history_action = None
        if undo or redo:
            operations = []
            if undo_applied or redo_applied:
                source = self.requirement_history[sid] if undo else self.requirement_future[sid]
                destination = self.requirement_future[sid] if undo else self.requirement_history[sid]
                previous = source.pop()
                reset_meta = previous.get('_reset_meta')
                destination_entry = deepcopy(before)
                if reset_meta:
                    destination_entry['_reset_meta'] = {
                        'turn': reset_meta['turn'], 'kind': reset_meta.get('kind', 'reset'),
                        **product_before,
                    }
                    reset_history_action = {'action': 'undo' if undo else 'redo',
                                            'turn': reset_meta['turn'],
                                            'kind': reset_meta.get('kind', 'reset')}
                destination.append(destination_entry)
                for name in requirement_fields:
                    setattr(state, name, deepcopy(previous[name]))
                if reset_meta:
                    for name in product_before:
                        setattr(state, name, deepcopy(reset_meta[name]))
        incoming_category = next((u.values[0] for u in operations if u.slot == "category" and u.operation == "set"), None)
        old_category = state.hard_slots.get("category")
        category_changed = bool(old_category and incoming_category and old_category.value != incoming_category)
        if category_changed:
            if state.active_item_price_category:
                for slot in ('price_min', 'price_max'):
                    state.hard_slots.pop(slot, None)
                    if slot in state.shared_price_slots:
                        state.hard_slots[slot] = deepcopy(state.shared_price_slots[slot])
            # A new product target retains explicit monetary limits, not old
            # product-specific material, color, brand or feature requirements.
            state.hard_slots = {k: v for k, v in state.hard_slots.items() if k in {"price_min", "price_max"}}
            state.soft_slots = {k: v for k, v in state.soft_slots.items()
                                if k in {'budget_target', 'budget_floor_target',
                                         'shopping_purpose', 'shopping_occasion'}}
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
        conflicts = []
        explicitly_cleared = {u.slot for u in operations if u.operation == 'clear'}
        protected_conflicts = {u.slot for u in operations
            if u.operation == 'set' and u.constraint_type == 'soft'
            and u.slot in state.hard_slots and u.slot not in explicitly_cleared
            and set(u.values) - set(values(state.hard_slots[u.slot].value))}
        for update in operations:
            name, op = update.slot, update.operation
            if op == "clear":
                if name == 'all_requirements':
                    state.hard_slots.clear()
                    state.soft_slots.clear()
                    state.rejected_values.clear()
                    state.deferred_category_details.clear()
                    state.shown_asins.clear()
                    if search_reset_scope != 'search_only':
                        state.rejected_asins.clear()
                    state.clarification_count = 0
                elif name == "preferences":
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
            elif op == 'demote_soft':
                if name in state.soft_slots:
                    state.soft_slots[name].priority = min(state.soft_slots[name].priority, 0.35)
            elif op == 'promote_soft':
                if name in state.soft_slots:
                    state.soft_slots[name].priority = 1.0
                    state.soft_slots[name].priority_turn = turn
            elif op == 'remove_value':
                for slots in (state.hard_slots, state.soft_slots):
                    if name in slots:
                        remaining = [value for value in values(slots[name].value)
                                     if value not in update.values]
                        if remaining:
                            slots[name].value = remaining[0] if len(remaining) == 1 else tuple(remaining)
                        else:
                            slots.pop(name)
                            cleared.append(name)
            elif op == "set":
                target = state.hard_slots if update.constraint_type == "hard" else state.soft_slots
                other = state.soft_slots if update.constraint_type == "hard" else state.hard_slots
                # A weaker preference cannot erase an explicit hard requirement.
                if update.constraint_type == "soft" and name in state.hard_slots:
                    old_values = tuple(values(state.hard_slots[name].value))
                    if name in {'color', 'material', 'size', 'style', 'brand'} and set(update.values) - set(old_values):
                        conflicts.append({'slot': name, 'old': old_values, 'new': update.values})
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
                if name in protected_conflicts:
                    continue  # A tentative proposal does not yet authorize lifting exclusions.
                remaining = [v for v in state.rejected_values.get(name, []) if v not in update.values]
                if remaining:
                    state.rejected_values[name] = remaining
                else:
                    state.rejected_values.pop(name, None)
        if any(update.operation == 'set' and update.slot in {'price_min', 'price_max'}
               for update in operations):
            lower = state.hard_slots.get('price_min')
            upper = state.hard_slots.get('price_max')
            for name in ('budget_target', 'budget_floor_target'):
                preference = state.soft_slots.get(name)
                if preference is None:
                    continue
                try:
                    amount = float(preference.value)
                    conflicts_with_bound = ((lower is not None and amount < float(lower.value))
                                            or (upper is not None and amount > float(upper.value)))
                except (TypeError, ValueError):
                    conflicts_with_bound = True
                if conflicts_with_bound:
                    state.soft_slots.pop(name)
        if not undo and not redo:
            if incoming_category:
                incoming_scoped = state.deferred_category_details.get(incoming_category, {})
                state.active_item_price_category = (
                    incoming_category if any(slot in incoming_scoped for slot in ('price_min', 'price_max'))
                    else None)
            if search_reset:
                state.deferred_category_details.clear()
                state.shared_price_slots.clear()
                state.active_item_price_category = None
            elif turn_evidence.get('category_alternatives'):
                state.deferred_category_details = deepcopy(turn_evidence.get('category_scoped_details') or {})
            elif turn_evidence.get('deferred_item_edit'):
                edit = turn_evidence['deferred_item_edit']
                if edit['category'] in state.deferred_category_details:
                    scoped = state.deferred_category_details[edit['category']]
                    for slot in edit.get('remove', ()):
                        scoped.pop(slot, None)
                        scoped.pop(slot + '_exclude', None)
                    scoped.update(deepcopy(edit['details']))
                if edit.get('shared_updates'):
                    for scoped in state.deferred_category_details.values():
                        for slot in ('price_min', 'price_max'):
                            scoped.pop(slot, None)
                    state.active_item_price_category = None
            elif incoming_category and state.deferred_category_details and incoming_category not in state.deferred_category_details:
                state.deferred_category_details.clear()
                state.active_item_price_category = None
            else:
                active_category = state.hard_slots.get('category')
                active_name = active_category.value if active_category else None
                if active_name in state.deferred_category_details:
                    scoped = {}
                    slots = ['color', 'material', 'size', 'style', 'brand', 'subtype']
                    if state.active_item_price_category == active_name:
                        slots.extend(('price_min', 'price_max'))
                    for slot in slots:
                        current = state.hard_slots.get(slot) or state.soft_slots.get(slot)
                        if current is not None:
                            scoped[slot] = {'values': tuple(values(current.value)),
                                            'constraint_type': current.constraint_type.value,
                                            'evidence': current.evidence}
                    for slot, rejected in state.rejected_values.items():
                        if slot in {'color', 'material', 'size', 'style', 'brand'} and rejected:
                            scoped[slot + '_exclude'] = {'values': tuple(rejected),
                                                         'constraint_type': 'exclude'}
                    state.deferred_category_details[active_name] = scoped
            if state.active_item_price_category is None:
                state.shared_price_slots = {slot: deepcopy(state.hard_slots[slot])
                                            for slot in ('price_min', 'price_max')
                                            if slot in state.hard_slots}
        state.turn_id = turn
        # Ignore repeated facts and non-requirement messages, not just empty turns.
        def signature(fields):
            color = fields['soft_slots'].get('color')
            color_order = (tuple(preference.value for preference in
                                 weighted_soft_values('color', color, 1.0)) if color else ())
            return ({k: v.value for k, v in fields['hard_slots'].items()},
                    {k: (v.value, v.priority, v.priority_turn) for k, v in fields['soft_slots'].items()},
                    fields['rejected_values'], fields['deferred_category_details'], color_order)
        after = {name: getattr(state, name) for name in requirement_fields}
        previous_category = before['hard_slots'].get('category')
        current_category = state.hard_slots.get('category')
        category_changed = bool(previous_category and current_category
                                and previous_category.value != current_category.value)
        if category_changed and not reset_history_action:
            # Undo may also cross product targets. Old results must not exclude
            # matches for the restored target, and old questions are audit only.
            state.shown_asins.clear()
            state.rejected_asins.clear()
        changed = signature(before) != signature(after)
        if not undo and not redo and (signature(before) != signature(after) or
                                      search_reset_scope == 'everything'):
            history_entry = deepcopy(before)
            if search_reset_scope == 'everything' or category_changed:
                history_entry['_reset_meta'] = {
                    'turn': turn,
                    'kind': 'category_switch' if category_changed else 'reset',
                    **product_before,
                }
            self.requirement_history[sid].append(history_entry)
            self.requirement_future[sid].clear()
        state.summary = "; ".join([f"intent={state.intent.value}"] + [f"{k}={v.value}" for k, v in state.hard_slots.items()] + [f"prefer {k}={v.value}" for k, v in state.soft_slots.items()])
        self.sessions[sid] = state
        self.versions[sid] += 1
        pending_before = self.pending[sid]
        reset_now = bool(search_reset or reset_history_action and reset_history_action['action'] == 'redo')
        category_acquired = bool(current_category and not previous_category)
        streak = 0 if category_changed or category_acquired or reset_now else self.context[sid].get('clarification_streak', 0)
        question_scope_start = turn if category_changed or category_acquired or reset_now else self.context[sid].get('question_scope_start', 0)
        conversation_act = handoff['legacy_result'].get('decision_evidence', {}).get('conversation_act')
        evidence = handoff['legacy_result'].get('decision_evidence', {})
        question_focus = evidence.get('question_focus') or self.context[sid].get('pending_focus')
        if (evidence.get('question_focus') and state.hard_slots.get('category') and
                evidence['question_focus'] == self.context[sid].get('question_focus') and
                not changed):
            conversation_act = 'focus_repeat'
        if (pending_before and (pending_before.get('evidence') or {}).get('expected_reduction') is not None
                and evidence.get('requested_results') and not evidence.get('requested_more')
                and not changed and state.shown_asins):
            conversation_act = 'show_current_results'
        if ((undo and not undo_applied) or (redo and not redo_applied)) and state.shown_asins:
            conversation_act = 'history_noop'
        if (evidence.get('uncertain_preference') and not changed and state.shown_asins
                and not evidence.get('requested_more') and not evidence.get('negative_feedback')):
            conversation_act = 'uncertainty'
        self.pending[sid] = (pending_before if not reset_now and conversation_act not in {None, 'declined_options', 'unmatched_option', 'show_current_results', 'browse_again'}
                             else None)
        self.context[sid] = {"query": handoff["legacy_result"]["raw_query"], "category_changed": category_changed,
                             'conversation_act': conversation_act,
                             'requirements_changed': changed,
                             'requirement_conflicts': conflicts,
                             'clarification_streak': streak,
                             'question_scope_start': question_scope_start,
                             'clarification_stalled': bool(pending_before and not changed and not conflicts
                                                           and not question_focus),
                             'requested_results': handoff['legacy_result'].get('decision_evidence', {}).get('requested_results', False),
                             'requested_more': bool(evidence.get('requested_more')),
                             'declined_options': bool(evidence.get('declined_options')),
                             'accepted_options': bool(evidence.get('accepted_options')),
                             'browse_first': bool(evidence.get('browse_first')),
                             'decision_help': bool(evidence.get('decision_help')),
                             'bypassed_optional_question': evidence.get('bypassed_optional_question'),
                             'question_focus': question_focus,
                             'preference_priority_shift': evidence.get('preference_priority_shift'),
                             'category_alternatives': tuple(evidence.get('category_alternatives') or ()),
                             'category_scoped_details': evidence.get('category_scoped_details') or {},
                             'deferred_item_edit': evidence.get('deferred_item_edit'),
                             'pending_focus': question_focus if not state.hard_slots.get('category') else None,
                             "requirement_undo": ('applied' if undo_applied else 'empty') if undo else None,
                             "requirement_redo": ('applied' if redo_applied else 'empty') if redo else None,
                             "intent_changed": intent_changed, "cleared_slots": cleared,
                             "search_reset": search_reset,
                             "search_reset_scope": search_reset_scope,
                             "reset_history_action": reset_history_action,
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
            soft_preferences={k: weighted_soft_values(
                k, v, round(v.priority * v.confidence * 0.85 ** (
                    state.turn_id - max(v.source_turn, v.priority_turn or 0)), 6))
                for k, v in state.soft_slots.items()},
            exclusions=state.rejected_values, slot_metadata=metadata,
            profile_hints={"preference_tags": list(self.profiles[sid].get("preference_tags", []))},
            session_summary=state.summary, shown_asins=tuple(state.shown_asins),
            rejected_asins=tuple(state.rejected_asins),
            suggestions={**self.context[sid], 'deferred_category_details': deepcopy(state.deferred_category_details),
                         "clarification_count": state.clarification_count, "max_turns": self.max_turns,
                         'can_undo_requirements': bool(self.requirement_history[sid]),
                         'can_redo_requirements': bool(self.requirement_future[sid])},
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
        if not self.context[sid].get('conversation_act'):
            self.context[sid]['clarification_streak'] = self.context[sid].get('clarification_streak', 0) + 1 if question else 0
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
