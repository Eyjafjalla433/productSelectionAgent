import unittest

from mvp.demo import DEMO_CATALOG
from mvp.server import AgentRuntime
from submission_tools.abcd_evaluate import _run_product_session, _selected_samples


class FakeEvaluator:
    @staticmethod
    def materialize_hidden_fields(sample, products):
        return sample["intent_card"], sample.get("behavior", {})

    @staticmethod
    def coarse_category(categories):
        return "running shoes"

    @staticmethod
    def initial_message(sample, category, disclosed):
        return "I need breathable running shoes under $100."

    @staticmethod
    def customer_reply(sample, ask_attribute, disclosed, boundary_used):
        return "Prefer lightweight mesh, and no leather.", boundary_used


class ABCDEvaluationTests(unittest.TestCase):
    def test_subset_selection_is_deterministic_and_scenario_balanced(self):
        samples = [
            {"sample_id": f"{kind}-{index}", "scenario_type": kind}
            for kind in ("buying", "browsing", "boundary", "intent_override")
            for index in range(3)
        ]
        selected = _selected_samples(samples, 8)
        self.assertEqual(len(selected), 8)
        self.assertEqual(
            {kind: sum(row["scenario_type"] == kind for row in selected) for kind in {row["scenario_type"] for row in selected}},
            {"boundary": 2, "browsing": 2, "buying": 2, "intent_override": 2},
        )
        self.assertEqual(selected, _selected_samples(samples, 8))

    def test_real_runtime_exercises_b_c_and_d_contracts(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode="adaptive")
        target = "DEMO-SHOE-001"
        sample = {
            "sample_id": "demo-public",
            "scenario_type": "buying",
            "ground_truth": {"parent_asin": target},
            "user_profile": {
                "average_prior_rating": 5.0,
                "preference_tags": ["comfort"],
                "purchase_frequency": "repeat",
                "rating_style": "positive",
                "summary": "Comfort matters.",
            },
            "intent_card": {"hard_constraints": [], "soft_preferences": []},
        }
        products = {target: {"parent_asin": target}}
        row = _run_product_session(
            runtime,
            FakeEvaluator,
            sample,
            {target: ["Shoes", "Running"]},
            products,
        )
        self.assertEqual(row["errors"], [])
        self.assertGreaterEqual(row["product_count"], 2)
        self.assertTrue(row["finalized"])
        self.assertIn("product_description", row["detail_fields"])
        self.assertEqual(row["model_tokens"], 0)
        self.assertGreater(row["trace_stages"].get("1_intent", 0), 0)
        self.assertGreater(row["trace_stages"].get("4B_ranking", 0), 0)


if __name__ == "__main__":
    unittest.main()
