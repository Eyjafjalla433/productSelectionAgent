from __future__ import annotations

import unittest

from intent_router import IntentRouter


class IntentRouterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = IntentRouter(known_brands=["Nike", "Columbia"])

    def test_buying_query_extracts_filterable_constraints(self) -> None:
        result = self.router.understand(
            "I'm ready to buy shoes under $90; they must be black and Nike only."
        )
        self.assertEqual(result.intent_type, "buying")
        self.assertEqual(result.hard_constraints["budget_max"], 90.0)
        self.assertIn("black", result.hard_constraints["color"])
        self.assertIn("nike", result.hard_constraints["brand"])
        self.assertIn("shoes", result.hard_constraints["category"])
        self.assertIn("budget_max", result.filter_constraints)
        self.assertNotIn("color", result.filter_constraints)

    def test_browsing_query_preserves_semantic_preferences(self) -> None:
        result = self.router.understand(
            "I'm still exploring ideas for comfortable outfits for a summer wedding."
        )
        self.assertEqual(result.intent_type, "browsing")
        self.assertIn("wedding", result.soft_preferences["use_case"])
        self.assertIn("comfortable", result.soft_preferences["feature"])

    def test_override_is_signaled_without_owning_state(self) -> None:
        result = self.router.understand(
            "Actually, ignore my earlier preference. What I need is a wool winter jacket."
        )
        self.assertTrue(result.override_detected)
        self.assertIsNone(result.intent_type)
        self.assertIn("wool", result.soft_preferences["material"])

    def test_catalog_simulator_buying_message(self) -> None:
        result = self.router.understand(
            "I'm looking for Shirts T-Shirts. A key requirement is: cotton."
        )
        self.assertIsNone(result.intent_type)
        self.assertIn("cotton", result.hard_constraints["material"])

    def test_catalog_simulator_browsing_message(self) -> None:
        result = self.router.understand(
            "I'm looking for Earrings Hoop, but I'm still exploring."
        )
        self.assertEqual(result.intent_type, "browsing")
        self.assertIn("earrings", result.slots["category"])

    def test_budget_target_is_a_soft_preference(self) -> None:
        result = self.router.understand("Show me casual dresses around $60.")
        self.assertIsNone(result.intent_type)
        self.assertEqual(result.soft_preferences["budget_target"], 60.0)

    def test_size_style_and_audience_are_extracted(self) -> None:
        result = self.router.understand(
            "I need a women's size 8 casual dress under $75."
        )
        self.assertIn("women", result.slots["audience"])
        self.assertIn("8", result.hard_constraints["size"])
        self.assertIn("casual", result.slots["style"])
        self.assertEqual(result.hard_constraints["budget_max"], 75.0)

    def test_negative_constraints_are_hard_filters(self) -> None:
        result = self.router.understand(
            "I need hiking shoes under $100, not leather and no heels."
        )
        self.assertIn("leather", result.hard_constraints["material_exclude"])
        self.assertIn("shoes", result.hard_constraints["category_exclude"])
        self.assertNotIn("material_exclude", result.filter_constraints)

    def test_decision_evidence_explains_browsing_intent(self) -> None:
        result = self.router.understand("What should I wear? I'm still exploring ideas.")
        self.assertEqual(result.intent_type, "browsing")
        self.assertIn("still_exploring", result.decision_evidence["browsing"])
        self.assertIn("missing_category", result.ambiguity_flags)

    def test_disclosed_unstructured_requirement_is_preserved_as_feature(self) -> None:
        result = self.router.understand(
            "I'm looking for earrings. A key requirement is: Snap closure."
        )
        self.assertIn("snap closure", result.slots["feature"])
        self.assertIn("snap closure", result.hard_constraints["feature"])
        self.assertNotIn("m", result.slots.get("size", []))

    def test_constraints_do_not_force_buying_intent(self) -> None:
        result = self.router.understand("I want something for hiking under $100.")
        self.assertIsNone(result.intent_type)
        self.assertEqual(result.hard_constraints["budget_max"], 100.0)
        self.assertIn("intent_undetermined", result.ambiguity_flags)

    def test_chinese_query_maps_common_terms_to_catalog_vocabulary(self) -> None:
        result = self.router.understand("我想要一条蓝色棉质连衣裙，价格不超过50美元。")
        self.assertEqual(result.slots["category"], ["dress"])
        self.assertEqual(result.slots["color"], ["blue"])
        self.assertEqual(result.slots["material"], ["cotton"])
        self.assertEqual(result.slots["budget_max"], 50.0)

    def test_chinese_range_and_exclusion_are_structured(self) -> None:
        result = self.router.understand("帮我找50到80美元的黑色鞋，不要皮革。")
        self.assertEqual(result.slots["category"], ["shoes"])
        self.assertEqual(result.slots["color"], ["black"])
        self.assertEqual(result.slots["budget_min"], 50.0)
        self.assertEqual(result.slots["budget_max"], 80.0)
        self.assertEqual(result.slots["material_exclude"], ["leather"])

    def test_chinese_rmb_is_not_silently_treated_as_catalog_dollars(self) -> None:
        result = self.router.understand("我想找一条预算300元以内的连衣裙。")
        self.assertEqual(result.slots["category"], ["dress"])
        self.assertNotIn("budget_max", result.slots)


if __name__ == "__main__":
    unittest.main()
