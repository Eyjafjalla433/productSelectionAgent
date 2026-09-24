from __future__ import annotations

from .extractor import Extraction
from .models import ConstraintType, Intent, SessionState, Slot, StateDelta


COMPATIBLE_SLOTS = {
    "shoes": {"category", "color", "price_max", "price_min", "occasion", "style", "material", "gender", "size"},
    "dress": {"category", "color", "price_max", "price_min", "occasion", "style", "material", "gender", "size"},
    "handbag": {"category", "color", "price_max", "price_min", "occasion", "style", "material", "gender"},
    "jewelry": {"category", "color", "price_max", "price_min", "occasion", "style", "material", "gender"},
    "top": {"category", "color", "price_max", "price_min", "occasion", "style", "material", "gender", "size"},
    "outerwear": {"category", "color", "price_max", "price_min", "occasion", "style", "material", "gender", "size"},
}


class DynamicStateMachine:
    def apply(self, state: SessionState, extraction: Extraction, utterance: str) -> StateDelta:
        state.turn_id += 1
        delta = StateDelta()
        previous_category = state.hard_slots.get("category")
        incoming_category = next((s.value for s in extraction.slots if s.name == "category"), None)

        if incoming_category and previous_category and incoming_category != previous_category.value:
            delta.category_overridden = True
            self._erase_incompatible_slots(state, str(incoming_category), delta)
        if extraction.intent != Intent.UNKNOWN and extraction.intent != state.intent:
            delta.intent_changed = state.intent != Intent.UNKNOWN
            state.intent = extraction.intent
            state.intent_confidence = extraction.confidence

        for name in extraction.cleared_slots:
            slot_names = ("price_min", "price_max") if name == "budget" else (name,)
            for slot_name in slot_names:
                for slots in (state.hard_slots, state.soft_slots):
                    if slot_name in slots:
                        del slots[slot_name]
                        delta.erased_slots.append(slot_name)
                # "Any colour is fine" also withdraws a previous rejection.
                state.rejected_values.pop(slot_name, None)

        for name, values in extraction.rejected.items():
            for value in values:
                state.rejected_values.setdefault(name, [])
                if value not in state.rejected_values[name]:
                    state.rejected_values[name].append(value)
                    delta.rejected_values.append(f"{name}={value}")
                existing = state.hard_slots.get(name)
                if existing and existing.value == value:
                    del state.hard_slots[name]
                    delta.erased_slots.append(name)

        for extracted in extraction.slots:
            target = state.hard_slots if extracted.constraint_type == ConstraintType.HARD else state.soft_slots
            existing = target.get(extracted.name)
            slot = Slot(
                value=extracted.value,
                source_turn=state.turn_id,
                confidence=extracted.confidence,
                priority=1.0,
                constraint_type=extracted.constraint_type,
                evidence=utterance,
            )
            if existing is None:
                target[extracted.name] = slot
                delta.added_slots.append(f"{extracted.name}={extracted.value}")
            elif existing.value != extracted.value:
                target[extracted.name] = slot
                delta.updated_slots.append(f"{extracted.name}:{existing.value}->{extracted.value}")
            else:
                target[extracted.name] = slot
        self._update_stage(state, extraction)
        state.summary = self._summary(state)
        return delta

    def _erase_incompatible_slots(self, state: SessionState, category: str, delta: StateDelta) -> None:
        allowed = COMPATIBLE_SLOTS.get(category, set())
        # Catalog leaf categories such as "Running Shoes" are not part of the
        # compact hand-maintained compatibility table.  Preserve accumulated
        # cross-category constraints instead of erasing everything.
        if not allowed:
            return
        for slots in (state.hard_slots, state.soft_slots):
            for name in list(slots):
                if name not in allowed:
                    del slots[name]
                    delta.erased_slots.append(name)

    @staticmethod
    def _update_stage(state: SessionState, extraction: Extraction) -> None:
        if extraction.conversion:
            state.decision_stage = "conversion"
        elif extraction.comparison:
            state.decision_stage = "comparison"
        elif state.intent == Intent.BROWSING:
            state.decision_stage = "browsing"
        elif state.intent == Intent.BUYING:
            state.decision_stage = "constraint_gathering"
        else:
            # Comparison and conversion are current-turn actions, not sticky
            # modes: a later follow-up must be allowed to retrieve again.
            state.decision_stage = "retrieval"

    @staticmethod
    def _summary(state: SessionState) -> str:
        parts = [f"intent={state.intent.value}"]
        parts += [f"{k}={v.value}" for k, v in state.hard_slots.items()]
        parts += [f"prefer {k}={v.value}" for k, v in state.soft_slots.items()]
        parts += [f"avoid {k}={','.join(map(str, values))}" for k, values in state.rejected_values.items()]
        return "; ".join(parts)
