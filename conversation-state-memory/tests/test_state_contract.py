from dataclasses import asdict, replace
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from state_memory import StateSnapshotV2
from state_memory.models import ConstraintType, ContextSnapshot, Intent, NextAction, Route, SessionState, Slot


class StateContractTests(unittest.TestCase):
    def setUp(self):
        self.session = SessionState(
            session_id="s", turn_id=3, intent=Intent.BUYING, intent_confidence=0.8,
            hard_slots={"price_max": Slot(50.0, 1, confidence=0.9, evidence="under $50")},
            soft_slots={"style": Slot("casual", 2, constraint_type=ConstraintType.SOFT)},
            rejected_values={"color": ["black"]}, clarification_count=1,
            shown_asins=["P1"],
        )
        self.snapshot = ContextSnapshot(
            query="blue instead", intent=Intent.BUYING, route=Route.BUYING_FILTER,
            action=NextAction.RETRIEVE_BUYING, must_match={"price_max": 50.0},
            should_match={"style": {"casual": 0.85}},
            must_not_match={"color": ["black"]}, profile_hints={"brand": ["Example"]},
            clarification_question=None, retrieval_budget=100, session_summary="dress under $50",
        )

    def export(self, **kwargs):
        return StateSnapshotV2.from_legacy(self.snapshot, session=self.session, state_version=7, **kwargs)

    def test_export_preserves_hard_price_exclusions_weights_and_provenance(self):
        payload = json.loads(json.dumps(self.export().to_dict(), allow_nan=False))
        self.assertEqual(payload["hard_constraints"], {"price_max": 50.0})
        self.assertEqual(payload["exclusions"], {"color": ["black"]})
        self.assertEqual(payload["soft_preferences"]["style"], [{"value": "casual", "weight": 0.85}])
        self.assertEqual(payload["slot_metadata"]["hard"]["price_max"]["source_turn"], 1)
        self.assertEqual(payload["slot_metadata"]["hard"]["price_max"]["evidence"], "under $50")
        self.assertEqual(payload["slot_metadata"]["soft"]["style"]["constraint_type"], "soft")
        self.assertNotIn("price_max", payload["soft_preferences"])

    def test_export_is_detached_and_does_not_execute_policy_or_change_turn(self):
        original_session = asdict(self.session)
        original_snapshot = asdict(self.snapshot)
        exported = self.export()
        exported.exclusions["color"].append("red")
        exported.hard_constraints["price_max"] = 100
        exported.slot_metadata["hard"]["price_max"]["source_turn"] = 99
        self.assertEqual(asdict(self.session), original_session)
        self.assertEqual(asdict(self.snapshot), original_snapshot)
        wire = exported.to_dict()
        wire["exclusions"]["color"].append("green")
        self.assertNotIn("green", exported.exclusions["color"])

    def test_version_is_not_inferred_from_turn_and_unknown_history_is_explicit(self):
        exported = self.export()
        self.assertEqual((exported.turn, exported.state_version), (3, 7))
        self.assertIsNone(exported.asked_questions)
        self.assertEqual(self.export(asked_questions=()).asked_questions, ())
        self.assertIn("action", exported.suggestions)
        self.assertFalse(hasattr(exported, "action"))

    def test_execution_history_is_copied_not_inferred_from_suggestions(self):
        asked = ({"question_id": "q1", "target_slot": "color", "question_text": "Which color?"},)
        exported = self.export(asked_questions=asked, pending_question=asked[0])
        asked[0]["target_slot"] = "brand"
        self.assertEqual(exported.pending_question["target_slot"], "color")
        self.assertEqual(exported.shown_asins, ("P1",))

    def test_mismatched_snapshot_and_invalid_versions_are_rejected(self):
        self.snapshot.must_match["price_max"] = 70
        with self.assertRaises(ValueError):
            self.export()
        self.snapshot.must_match["price_max"] = 50
        for version in (0, True, 1.5):
            with self.subTest(version=version), self.assertRaises(ValueError):
                replace(self.export(), state_version=version)

    def test_mismatched_intent_soft_values_and_exclusions_are_rejected(self):
        for changes in (
            {"intent": Intent.BROWSING},
            {"must_not_match": {"color": ["red"]}},
            {"should_match": {"style": {"formal": 0.85}}},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                StateSnapshotV2.from_legacy(replace(self.snapshot, **changes), session=self.session, state_version=7)

    def test_numeric_soft_values_remain_numbers_after_json_serialization(self):
        self.session.soft_slots["size"] = Slot(42, 2, constraint_type=ConstraintType.SOFT)
        self.snapshot.should_match["size"] = {42: 0.75}
        wire = json.loads(json.dumps(self.export().to_dict(), allow_nan=False))
        self.assertEqual(wire["soft_preferences"]["size"], [{"value": 42, "weight": 0.75}])
        self.assertIsInstance(wire["soft_preferences"]["size"][0]["value"], int)


if __name__ == "__main__":
    unittest.main()
