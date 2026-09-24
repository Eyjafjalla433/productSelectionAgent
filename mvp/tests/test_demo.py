import unittest

from mvp.audit import verify_audit
from mvp.demo import DEMO_CASES, DEMO_CATALOG, DEMO_WEB_SCENARIOS
from mvp.server import AgentRuntime


class FakeCatalogDemoTests(unittest.TestCase):
    def test_all_scripted_cases_finish_with_evidence_and_zero_tokens(self):
        runtime = AgentRuntime.create(
            DEMO_CATALOG,
            orchestration_mode="adaptive",
            scenarios=DEMO_WEB_SCENARIOS,
        )
        self.assertEqual(runtime.agent.catalog_size, 15)
        self.assertEqual(
            [scenario["id"] for scenario in runtime.new_session()["scenarios"]],
            list(DEMO_CASES),
        )

        for name, case in DEMO_CASES.items():
            with self.subTest(case=name):
                session_id = runtime.new_session()["session_id"]
                results = [runtime.chat(session_id, prompt) for prompt in case["prompts"]]
                first_products = results[0]["products"]
                self.assertGreaterEqual(len(first_products), 2)
                self.assertLessEqual(len(first_products), 10)
                self.assertTrue(first_products[0]["advice"]["pros"])
                self.assertTrue(first_products[0]["advice"]["cons"])
                self.assertTrue(
                    all(
                        (result["assistant"]["usage"].get("prompt_tokens") or 0)
                        + (result["assistant"]["usage"].get("completion_tokens") or 0)
                        == 0
                        for result in results
                    )
                )
                self.assertEqual(results[-1]["selection_state"]["status"], "finalized")
                handoff = runtime.selection_handoff(session_id)
                self.assertEqual(handoff["status"], "finalized")
                self.assertGreaterEqual(len(handoff["selected_products"]), 1)
                self.assertEqual(
                    len(handoff["comparison_summary"]["rows"]),
                    len(handoff["selected_products"]),
                )
                self.assertIsNotNone(handoff["comparison_summary"]["lowest_price_asin"])
                self.assertIsNotNone(handoff["comparison_summary"]["highest_rating_asin"])
                self.assertEqual(verify_audit(runtime.audit(session_id)), [])

    def test_feedback_refresh_and_category_switch_keep_session_state_consistent(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode="adaptive")

        session_id = runtime.new_session()["session_id"]
        first = runtime.chat(session_id, "I need running shoes under $100")
        rejected_asin = first["products"][0]["parent_asin"]
        rejected = runtime.chat(session_id, "I don't like #1")
        self.assertIn(rejected_asin, rejected["selection_state"]["rejected_asins"])
        refreshed = runtime.chat(session_id, "Show me more running shoes")
        self.assertNotIn(rejected_asin, [row["parent_asin"] for row in refreshed["products"]])

        session_id = runtime.new_session()["session_id"]
        runtime.chat(session_id, "I need a blue cotton dress under $50")
        compared = runtime.chat(session_id, "Compare #1 and #2")
        self.assertEqual(compared["selection_state"]["selection_count"], 2)
        switched = runtime.chat(session_id, "Switch to running shoes, not leather")
        self.assertTrue(switched["receipt"]["intent_reset"])
        self.assertTrue(switched["receipt"]["selection_reset"])
        self.assertEqual(switched["selection_state"]["selection_count"], 0)


if __name__ == "__main__":
    unittest.main()
