import sys
import unittest
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from state_memory import NextAction, Route, StateMemoryManager


class StateMemoryManagerTests(unittest.TestCase):
    def setUp(self):
        self.manager = StateMemoryManager()

    def update(self, text, feedback=None):
        return self.manager.update("s1", "u1", text, feedback)

    def test_accumulates_hard_and_soft_constraints(self):
        self.update("I need a dress for work")
        self.update("black and under $50")
        snapshot = self.update("something slimming")
        self.assertEqual(snapshot.must_match["category"], "dress")
        self.assertEqual(snapshot.must_match["occasion"], "work")
        self.assertEqual(snapshot.must_match["color"], "black")
        self.assertEqual(snapshot.must_match["price_max"], 50.0)
        self.assertIn("slimming", snapshot.should_match["style"])

    def test_replaces_color_and_keeps_explicit_rejection(self):
        self.update("show me a black dress")
        snapshot = self.update("not black, blue instead")
        self.assertEqual(snapshot.must_match["color"], "blue")
        self.assertIn("black", snapshot.must_not_match["color"])

    def test_category_override_reroutes(self):
        self.update("I need a work dress under $50")
        snapshot = self.update("actually show me running shoes")
        self.assertEqual(snapshot.must_match["category"], "shoes")
        self.assertEqual(snapshot.action, NextAction.REROUTE)

    def test_budget_is_rewritten(self):
        self.update("a dress under $50")
        snapshot = self.update("I can go up to $80")
        self.assertEqual(snapshot.must_match["price_max"], 80.0)

    def test_profile_does_not_override_current_hard_constraint(self):
        self.update("I need a black dress")
        self.update("another black work dress")
        snapshot = self.manager.update("s2", "u1", "show me a blue dress")
        self.assertEqual(snapshot.must_match["color"], "blue")
        self.assertNotIn("color", snapshot.profile_hints)

    def test_overload_asks_high_information_question(self):
        snapshot = self.update("show me something nice", {"candidate_count": 1000})
        self.assertEqual(snapshot.action, NextAction.ASK_CLARIFICATION)
        self.assertIn("type of item", snapshot.clarification_question)

    def test_browsing_and_buying_routes(self):
        snapshot = self.update("what bags are trending this season")
        self.assertEqual(snapshot.route, Route.BROWSING_DENSE)
        snapshot = self.update("show me a black tote under $80")
        self.assertEqual(snapshot.route, Route.BUYING_FILTER)

    def test_zero_results_preserves_hard_constraints_and_relaxes_soft_ones(self):
        self.update("I need a slimming blue dress under $80")
        snapshot = self.update("please search again", {"candidate_count": 0})
        self.assertEqual(snapshot.must_match["category"], "dress")
        self.assertEqual(snapshot.must_match["color"], "blue")
        self.assertEqual(snapshot.debug["relax_soft_preferences"], ["style"])

    def test_soft_preference_weight_decays_over_turns(self):
        first = self.update("I need a minimal dress")
        first_weight = first.should_match["style"]["minimal"]
        self.update("for work")
        later = self.update("under $80")
        self.assertLess(later.should_match["style"]["minimal"], first_weight)

    def test_conversion_signal(self):
        self.update("show me a dress")
        snapshot = self.update("I'll take this")
        self.assertEqual(snapshot.action, NextAction.CONVERT)

    def test_comparison_action_is_not_sticky_on_follow_up(self):
        self.update("compare these dresses")
        snapshot = self.update("blue please")
        self.assertEqual(snapshot.action, NextAction.RETRIEVE_BUYING)

    def test_overload_stops_prompting_when_all_priority_slots_are_present(self):
        self.update("show me a blue work dress under $80", {"candidate_count": 1000})
        snapshot = self.update("women size M", {"candidate_count": 1000})
        self.assertNotEqual(snapshot.action, NextAction.ASK_CLARIFICATION)

    def test_explicitly_clearing_a_constraint_removes_old_slot_and_rejection(self):
        self.update("I need a black dress under $80")
        snapshot = self.update("Any color is fine; ignore my earlier budget.")
        self.assertNotIn("color", snapshot.must_match)
        self.assertNotIn("price_max", snapshot.must_match)
        self.assertNotIn("color", snapshot.must_not_match)
        self.assertIn("color", snapshot.debug["erased_slots"])
        self.assertIn("price_max", snapshot.debug["erased_slots"])

    def test_replacing_color_does_not_imply_the_old_color_is_rejected(self):
        self.update("I need a black dress")
        snapshot = self.update("Actually, blue instead")
        self.assertEqual(snapshot.must_match["color"], "blue")
        self.assertNotIn("color", snapshot.must_not_match)

    def test_clarification_is_capped_and_retrieval_is_forced_before_turn_limit(self):
        snapshot = self.update("show me something", {"candidate_count": 1000})
        self.assertEqual(snapshot.action, NextAction.ASK_CLARIFICATION)
        snapshot = self.update("still browsing", {"candidate_count": 1000})
        self.assertEqual(snapshot.action, NextAction.ASK_CLARIFICATION)
        snapshot = self.update("show more", {"candidate_count": 1000})
        self.assertIn(
            snapshot.action,
            {NextAction.RETRIEVE_BUYING, NextAction.RETRIEVE_BROWSING},
        )
        self.assertEqual(snapshot.debug["clarification_count"], 2)

        for _ in range(5):
            snapshot = self.update("still looking", {"candidate_count": 1000})
        self.assertGreaterEqual(self.manager.sessions["s1"].turn_id, 8)
        self.assertNotEqual(snapshot.action, NextAction.ASK_CLARIFICATION)
        self.assertTrue(snapshot.debug["forced_retrieval"])

    def test_catalog_aware_extraction_captures_brand_feature_and_rating(self):
        snapshot = self.update("I need Columbia waterproof shoes rated 4 stars")
        self.assertEqual(snapshot.must_match["brand"], "Columbia")
        self.assertTrue(snapshot.must_match["feature_waterproof"])
        self.assertEqual(snapshot.must_match["rating_min"], 4.0)

    def test_catalog_aware_extraction_captures_apparel_attributes(self):
        snapshot = self.update("I need a floral long sleeve relaxed dress")
        self.assertIn("floral", snapshot.should_match["pattern"])
        self.assertIn("long sleeve", snapshot.should_match["sleeve"])
        self.assertIn("relaxed", snapshot.should_match["fit"])

    def test_catalog_details_expand_structured_attribute_vocabulary(self):
        product = {
            "parent_asin": "test",
            "categories": ["Clothing, Shoes & Jewelry", "Women", "Dresses", "Wrap Dresses"],
            "store": "Example Brand",
            "title": "Example dress",
            "features": [],
            "details": {
                "Closure Type": "Zipper",
                "Neck Style": "V Neck",
                "Product Care Instructions": "Machine Wash",
                "Sport Type": "Running",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            cache_path = Path(directory) / "cache" / "lexicon.json"
            catalog_path.write_text(json.dumps(product) + "\n", encoding="utf-8")
            manager = StateMemoryManager(
                catalog_path=catalog_path,
                catalog_cache_path=cache_path,
            )
            snapshot = manager.update(
                "catalog-session",
                "catalog-user",
                "Show an Example Brand wrap dresses with zipper, v neck, machine wash for running",
            )
        self.assertEqual(snapshot.must_match["brand"], "Example Brand")
        self.assertEqual(snapshot.must_match["closure"], "Zipper")
        self.assertEqual(snapshot.must_match["neckline"], "V Neck")
        self.assertIn("Machine Wash", snapshot.should_match["care"])
        self.assertIn("Running", snapshot.should_match["sport"])

    def test_catalog_lexicon_is_cached_on_disk_and_invalidated_on_change(self):
        product = {
            "parent_asin": "test",
            "categories": ["Clothing", "Jackets"],
            "store": "Cache Brand",
            "title": "Test jacket",
            "features": ["Waterproof"],
            "details": {"Closure Type": "Zipper"},
        }
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            cache_path = Path(directory) / "cache" / "lexicon.json"
            catalog_path.write_text(json.dumps(product) + "\n", encoding="utf-8")
            manager = StateMemoryManager(catalog_path=catalog_path, catalog_cache_path=cache_path)
            self.assertTrue(cache_path.is_file())
            self.assertEqual(manager.update("cache-1", "user", "Cache Brand zipper jacket").must_match["closure"], "Zipper")

            product["details"] = {"Closure Type": "Buttons"}
            catalog_path.write_text(json.dumps(product) + "\n", encoding="utf-8")
            refreshed = StateMemoryManager(catalog_path=catalog_path, catalog_cache_path=cache_path)
            snapshot = refreshed.update("cache-2", "user", "Cache Brand buttons jacket")
            self.assertEqual(snapshot.must_match["closure"], "Buttons")


if __name__ == "__main__":
    unittest.main()
