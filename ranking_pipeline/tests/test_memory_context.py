from __future__ import annotations

import unittest
from types import SimpleNamespace

from state_memory import StateMemoryManager

from ranking_pipeline.memory_context import (
    intent_to_context,
    intent_to_requirements,
    merge_profile_with_snapshot,
    snapshot_to_requirements,
)


class MemoryContextTests(unittest.TestCase):
    def test_snapshot_to_requirements_uses_state_memory_contract(self) -> None:
        manager = StateMemoryManager()
        snapshot = manager.update(
            "session",
            "user",
            "I need a blue dress for work under $80",
        )

        requirements = snapshot_to_requirements(snapshot)

        self.assertEqual(requirements.category, "dress")
        self.assertIn("color: blue", requirements.hard_constraints)
        self.assertIn("occasion: work", requirements.hard_constraints)
        self.assertTrue(any("budget: under" in item for item in requirements.soft_preferences))

    def test_official_profile_keeps_priority_and_adds_snapshot_hints(self) -> None:
        snapshot = SimpleNamespace(
            profile_hints={"color": ["black"]},
            session_summary="intent=buying category=dress color=black",
        )

        profile = merge_profile_with_snapshot(
            {"preference_tags": ["fit"], "summary": "official summary"},
            snapshot,
        )

        self.assertIn("fit", profile["preference_tags"])
        self.assertIn("color:black", profile["preference_tags"])
        self.assertEqual(profile["summary"], "official summary")
        self.assertEqual(profile["conversation_summary"], snapshot.session_summary)

    def test_intent_to_context_keeps_ranking_relevant_fields(self) -> None:
        intent_result = SimpleNamespace(
            intent_type="buying",
            intent_confidence=0.85,
            route="filter_track",
            route_reason="confirmed_buying",
            override_detected=False,
            ambiguity_flags=["budget"],
            semantic_query="blue dress",
        )

        context = intent_to_context(intent_result)

        self.assertEqual(context["route"], "filter_track")
        self.assertEqual(context["intent_type"], "buying")
        self.assertEqual(context["ambiguity_flags"], ["budget"])

    def test_intent_to_requirements_uses_current_turn_contract(self) -> None:
        intent_result = SimpleNamespace(
            slots={"category": ["dress"]},
            hard_constraints={
                "category": ["dress"],
                "color": ["blue"],
                "budget_max": 80.0,
            },
            soft_preferences={"style": ["minimal"]},
        )

        requirements = intent_to_requirements(intent_result)

        self.assertEqual(requirements.category, "dress")
        self.assertIn("color: blue", requirements.hard_constraints)
        self.assertIn("style: minimal", requirements.soft_preferences)
        self.assertTrue(any("budget: under" in item for item in requirements.soft_preferences))

    def test_intent_to_requirements_falls_back_to_slots_category(self) -> None:
        intent_result = SimpleNamespace(
            slots={"category": ["dress"]},
            hard_constraints={"color": ["blue"]},
            soft_preferences={},
        )

        requirements = intent_to_requirements(intent_result)

        self.assertEqual(requirements.category, "dress")
        self.assertIn("color: blue", requirements.hard_constraints)


if __name__ == "__main__":
    unittest.main()
