from dataclasses import replace
import json
import unittest

from intent_router import IntentRouter, SlotUpdate


class HandoffContractTests(unittest.TestCase):
    def setUp(self):
        self.result = IntentRouter().understand("under $50")

    def test_existing_router_reports_unavailable_operations_not_no_change(self):
        payload = self.result.to_state_handoff(session_id="s", turn=2)
        self.assertIsNone(payload["slot_updates"])
        self.assertEqual(payload["intent"], "unknown")
        self.assertNotIn("route", payload)
        explicit = replace(self.result, slot_updates=()).to_state_handoff(session_id="s", turn=2)
        self.assertEqual(explicit["slot_updates"], [])

    def test_operations_preserve_distinct_meanings_and_numeric_budget(self):
        updates = (
            SlotUpdate("color", "set", ("blue",), "hard", 0.9, "blue instead"),
            SlotUpdate("budget_max", "set", (50.0,), "hard"),
            SlotUpdate("color", "exclude", ("black",)),
            SlotUpdate("color", "remove_exclusion", ("black",)),
            SlotUpdate("price_max", "clear"),
        )
        payload = replace(self.result, slot_updates=updates).to_state_handoff(session_id="s", turn=2)
        wire = json.loads(json.dumps(payload, allow_nan=False))
        self.assertEqual(wire["slot_updates"][1]["slot"], "price_max")
        self.assertEqual(wire["slot_updates"][1]["values"], [50.0])
        self.assertEqual(wire["slot_updates"][0]["evidence"], "blue instead")
        self.assertEqual([item["operation"] for item in wire["slot_updates"]], ["set", "set", "exclude", "remove_exclusion", "clear"])

    def test_legacy_serialization_unchanged_when_operations_are_attached(self):
        before = self.result.to_dict()
        enriched = replace(self.result, slot_updates=(SlotUpdate("price_max", "clear"),))
        self.assertEqual(enriched.to_dict(), before)
        self.assertNotIn("slot_updates", before)
        self.assertEqual(before["hard_constraints"]["budget_max"], 50.0)

    def test_invalid_operations_are_rejected(self):
        cases = [
            dict(slot="color", operation="set", values=("blue",)),
            dict(slot="color", operation="clear", values=("blue",)),
            dict(slot="color", operation="exclude"),
            dict(slot="color", operation="remove_exclusion", values=("blue",), constraint_type="soft"),
            dict(slot="price_max", operation="set", values=(float("nan"),), constraint_type="hard"),
            dict(slot="color", operation="set", values=("blue",), constraint_type="hard", confidence=2),
            dict(slot="color", operation="invent", values=("blue",)),
            dict(slot="color", operation="set", values="blue", constraint_type="hard"),
        ]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                SlotUpdate(**case)

    def test_handoff_requires_session_and_integer_turn(self):
        for session_id, turn in [("", 1), ("s", 0), ("s", True), ("s", 1.5)]:
            with self.subTest(session_id=session_id, turn=turn), self.assertRaises(ValueError):
                self.result.to_state_handoff(session_id=session_id, turn=turn)

    def test_untyped_operation_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            replace(self.result, slot_updates=({"slot": "color", "operation": "clear"},))


if __name__ == "__main__":
    unittest.main()
