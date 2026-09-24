from __future__ import annotations

import unittest

from ranking_pipeline.override_aware_agent import OverrideAwareRequirementsCollector


class OverrideAwareRequirementsCollectorTests(unittest.TestCase):
    def test_initial_override_message_parses_category_and_soft(self) -> None:
        collector = OverrideAwareRequirementsCollector()
        collector.observe("I'm looking for Accessories Belts. Buckle closure", turn=1)
        self.assertEqual(collector.category, "Accessories Belts")
        self.assertEqual(collector.soft_preferences, ["Buckle closure"])

    def test_same_slot_replacement_removes_only_conflicting_preference(self) -> None:
        collector = OverrideAwareRequirementsCollector()
        collector.category = "dress"
        collector.soft_preferences = ["red", "leather"]
        collector.soft_disclosed_order = ["red", "leather"]
        collector.observe("Actually, I need blue.", turn=3)
        self.assertEqual(collector.soft_preferences, ["leather"])
        self.assertEqual(collector.hard_constraints, ["blue"])

    def test_explicit_negation_removes_negated_preference(self) -> None:
        collector = OverrideAwareRequirementsCollector()
        collector.category = "dress"
        collector.soft_preferences = ["red"]
        collector.soft_disclosed_order = ["red"]
        collector.observe("Actually, not red. What I need is: blue.", turn=3)
        self.assertEqual(collector.soft_preferences, [])
        self.assertEqual(collector.hard_constraints, ["blue"])

    def test_generic_anaphora_removes_last_disclosed_soft(self) -> None:
        collector = OverrideAwareRequirementsCollector()
        collector.category = "dress"
        collector.soft_preferences = ["Imported", "Buckle closure"]
        collector.soft_disclosed_order = ["Imported", "Buckle closure"]
        collector.observe(
            "Actually, ignore my earlier preference. What I need is: leather.",
            turn=3,
        )
        self.assertEqual(collector.soft_preferences, ["Imported"])
        self.assertEqual(collector.hard_constraints, ["leather"])

    def test_no_override_does_not_pop_last_soft(self) -> None:
        collector = OverrideAwareRequirementsCollector()
        collector.category = "dress"
        collector.soft_preferences = ["red"]
        collector.soft_disclosed_order = ["red"]
        collector.observe("For that, what matters is: cotton.", turn=2)
        self.assertIn("red", collector.soft_preferences)


if __name__ == "__main__":
    unittest.main()
