"""Behavioral integration acceptance using real lexical indexes on a tiny catalog."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shopping_agent import FinalAgent
from shopping_agent.retrieval import RetrievalRequest, category_matches
from shopping_agent.policy import PreRetrievalPolicy
from intent_router.turn_router import TurnIntentRouter
from intent_router.models import SlotUpdate
from shopping_agent.requirement_enhancer import EnhancementOutcome


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.catalog = Path(cls.tmp.name) / "catalog.jsonl"
        rows = [
            ("BLACK", "black cotton dress", ["Clothing", "Dresses"], 30),
            ("BLUE", "blue cotton dress", ["Clothing", "Dresses"], 40),
            ("BLUE2", "blue linen dress", ["Clothing", "Dresses"], 45),
            ("EXPENSIVE", "blue cotton dress", ["Clothing", "Dresses"], 100),
            ("UNKNOWN", "blue cotton dress", ["Clothing", "Dresses"], None),
            ("SHOE", "canvas shoes comfortable", ["Shoes"], 25),
            ("LEATHER", "leather shoes", ["Shoes"], 20),
            ("JERSEY", "blue basketball jersey breathable mesh drawstring", ["Sports", "Jerseys"], 55),
            ("PAJAMA", "blue cotton jersey pajama pants", ["Clothing", "Pants"], 35),
        ]
        cls.catalog.write_text("\n".join(json.dumps({"parent_asin": asin, "title": title, "categories": cats,
            "price": price, "store": "Acme", "features": [], "details": {}, "description": [], "rating_number": 10}) for asin, title, cats, price in rows))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.agent = FinalAgent(self.catalog, trace_enabled=True)
        self.agent.reset("s", {"preference_tags": ["comfort"]})

    def respond(self, text, turn=1):
        result = self.agent.respond("s", text, turn, 10)
        self.assertEqual(self.agent.errors, [])
        return result

    def event(self, stage):
        return next(e["output"] for e in self.agent.trace[-1]["events"] if e["stage"] == stage)

    def test_catalog_capability_is_stable_and_returns_detached_records(self):
        self.assertEqual(self.agent.catalog_size, 9)
        product = self.agent.get_catalog_product("BLUE")
        self.assertEqual(product["title"], "blue cotton dress")
        product["title"] = "mutated outside A"
        self.assertEqual(self.agent.get_catalog_product("BLUE")["title"], "blue cotton dress")
        self.assertIsNone(self.agent.get_catalog_product("MISSING"))

    def test_real_pipeline_variable_candidates_and_budget(self):
        result = self.respond("I need a blue dress under $50.")
        self.assertEqual([r["parent_asin"] for r in result["recommendations"]], ["BLUE", "BLUE2"])
        self.assertEqual(self.event("2_retrieval")["returned_count"], 2)
        self.assertEqual(self.event("3_feedback")["shown_asins"], ("BLUE", "BLUE2"))

    def test_chinese_request_runs_offline_through_retrieval_and_ranking(self):
        result = self.respond("我想要一条蓝色棉质连衣裙，价格不超过50美元。")
        self.assertEqual([row["parent_asin"] for row in result["recommendations"]], ["BLUE"])
        state = self.event("3_state")
        self.assertEqual(state["hard_constraints"]["category"], "dress")
        self.assertEqual(state["hard_constraints"]["color"], "blue")
        self.assertEqual(state["hard_constraints"]["material"], "cotton")
        self.assertEqual(state["hard_constraints"]["price_max"], 50.0)
        self.assertEqual(result["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

    def test_taxonomy_category_matching_rejects_dress_shirts_for_dresses(self):
        dress_shirt = {
            "title": "Blue cotton dress shirt",
            "categories": ["Clothing, Shoes & Jewelry", "Men", "Clothing", "Shirts", "Dress Shirts"],
        }
        dress = {
            "title": "Blue cotton midi dress",
            "categories": ["Clothing, Shoes & Jewelry", "Women", "Clothing", "Dresses", "Casual"],
        }
        self.assertFalse(category_matches(dress_shirt, "dress")[0])
        self.assertTrue(category_matches(dress_shirt, "shirt")[0])
        self.assertTrue(category_matches(dress, "dress")[0])
        self.assertTrue(category_matches({"categories": ["Women", "Handbags & Wallets", "Totes"]}, "bag")[0])
        self.assertTrue(category_matches({"categories": ["Women", "Coats, Jackets & Vests"]}, "jacket")[0])
        self.assertFalse(category_matches({"categories": ["Shirts", "Short Sleeve"]}, "shorts")[0])

    def test_external_control_turn_preserves_sequence_without_state_mutation(self):
        first = self.respond("I need a blue dress under $50.", 1)
        state_before = self.agent.memory.snapshot("s").to_dict()
        control = self.agent.record_control(
            "s",
            "Compare #1 and #2",
            2,
            {"message": "Selected #1 and #2.", "usage": {"prompt_tokens": 5, "completion_tokens": 3}},
            {"action": "compare", "ranks": [1, 2]},
        )
        self.assertEqual(control["recommendations"], [])
        self.assertEqual(control["usage"], {"prompt_tokens": 5, "completion_tokens": 3})
        self.assertEqual(self.agent.trace[-1]["events"][0]["stage"], "0_control")
        self.assertEqual(self.agent.memory.snapshot("s").to_dict(), state_before)
        third = self.respond("Blue instead.", 3)
        self.assertIsInstance(third["recommendations"], list)
        self.assertEqual(first["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

    def test_drop_session_releases_calls_and_all_memory_fragments(self):
        self.respond("I need a blue dress under $50.", 1)
        self.assertEqual(self.agent.get_trace("s", 1)["session_id"], "s")
        self.agent.drop_session("s")
        self.assertNotIn("s", self.agent.calls)
        self.assertEqual(self.agent.get_session_traces("s"), [])
        with self.assertRaises(KeyError):
            self.agent.get_trace("s", 1)
        for collection in (
            self.agent.memory.sessions,
            self.agent.memory.profiles,
            self.agent.memory.versions,
            self.agent.memory.questions,
            self.agent.memory.pending,
            self.agent.memory.feedback_turns,
            self.agent.memory.context,
        ):
            self.assertNotIn("s", collection)
        with self.assertRaisesRegex(ValueError, "reset"):
            self.agent.respond("s", "Show me more", 2, 10)

    def test_explicit_product_feedback_excludes_rejected_asin_and_can_be_reversed(self):
        first = self.respond("I need a blue dress under $50.", 1)
        self.assertIn("BLUE", [row["parent_asin"] for row in first["recommendations"]])
        feedback = self.agent.record_product_feedback("s", source_turn=2, rejected_asins=("BLUE",))
        self.assertEqual(feedback.rejected_asins, ("BLUE",))
        second = self.respond("Show me more", 2)
        self.assertNotIn("BLUE", [row["parent_asin"] for row in second["recommendations"]])
        self.assertIn("Explicit feedback excluded", " ".join(self.event("2_retrieval")["warnings"]))

        restored = self.agent.record_product_feedback("s", source_turn=3, liked_asins=("BLUE",))
        self.assertEqual(restored.rejected_asins, ())
        third = self.respond("Show me more", 3)
        self.assertIn("BLUE", [row["parent_asin"] for row in third["recommendations"]])
        with self.assertRaisesRegex(ValueError, "shown"):
            self.agent.record_product_feedback("s", source_turn=4, rejected_asins=("NEVER_SHOWN",))

    def test_optional_model_updates_flow_through_state_and_usage(self):
        class StaticEnhancer:
            def enhance(self, message, deterministic):
                return EnhancementOutcome(
                    (SlotUpdate("feature", "set", ("extra pockets",), "soft", 0.7, "extra pockets"),),
                    "fake",
                    "fake-json",
                    {"prompt_tokens": 8, "completion_tokens": 2},
                    1.0,
                )

        self.agent.requirement_enhancer = StaticEnhancer()
        result = self.respond("I need a dress with extra pockets.")
        self.assertEqual(self.event("1_model_enhancement")["provider"], "fake")
        self.assertIn("feature", self.event("3_state")["soft_preferences"])
        self.assertEqual(result["usage"], {"prompt_tokens": 8, "completion_tokens": 2})

    def test_multiturn_override_clear_and_category_switch(self):
        self.respond("I need a black dress under $50.")
        self.respond("Blue instead.", 2)
        hard = self.event("3_state")["hard_constraints"]
        self.assertEqual(hard, {"category": "dress", "color": "blue", "price_max": 50.0})
        self.respond("No budget limit.", 3)
        self.assertNotIn("price_max", self.event("3_state")["hard_constraints"])
        result = self.respond("Switch to shoes, not leather.", 4)
        self.assertEqual(self.event("3_state")["shown_asins"], ())
        self.assertEqual(self.event("3_state")["hard_constraints"], {"category": "shoes"})
        self.assertEqual(self.event("3_state")["exclusions"], {"material": ["leather"]})
        self.assertEqual([r["parent_asin"] for r in result["recommendations"]], ["SHOE"])
        self.assertTrue(self.event("3_state")["suggestions"]["category_changed"])

    def test_adaptive_novelty_excludes_emitted_products_and_falls_back_when_exhausted(self):
        self.agent.reset("novelty", {})
        first = self.agent.respond("novelty", "I need a blue dress under $50.", 1, 1)
        first_asin = first["recommendations"][0]["parent_asin"]
        second = self.agent.respond("novelty", "Show me more results.", 2, 10)
        second_asins = [row["parent_asin"] for row in second["recommendations"]]
        self.assertNotIn(first_asin, second_asins)
        retrieval = next(event["output"] for event in self.agent.trace[-1]["events"] if event["stage"] == "2_retrieval")
        self.assertTrue(any("Novelty filter excluded" in warning for warning in retrieval["warnings"]))

        third = self.agent.respond("novelty", "Show me more results.", 3, 10)
        self.assertTrue(third["recommendations"])
        retrieval = next(event["output"] for event in self.agent.trace[-1]["events"] if event["stage"] == "2_retrieval")
        self.assertTrue(any("Novelty pool exhausted" in warning for warning in retrieval["warnings"]))

    def test_pre_policy_skips_retrieval_and_records_one_actual_question(self):
        result = self.respond("Help me find something.")
        self.assertEqual(result["ask_attribute"], "category")
        self.assertNotIn("2_retrieval", [e["stage"] for e in self.agent.trace[-1]["events"]])
        feedback = self.event("3_feedback")
        self.assertEqual(feedback["suggestions"]["clarification_count"], 1)
        self.assertEqual(feedback["pending_question"]["target_slot"], "category")
        self.respond("A black dress under $50.", 2)
        self.assertEqual(self.event("3_state")["state_version"], 3)
        self.assertEqual(self.event("3_feedback")["suggestions"]["clarification_count"], 1)

    def test_empty_pool_never_padded_and_question_limit(self):
        for turn in range(1, 11):
            response = self.respond("I need a purple dress under $1.", turn)
            self.assertEqual(response["recommendations"], [])
            if turn >= 3:
                self.assertIsNone(response["ask_attribute"])
        self.assertEqual(self.event("3_feedback")["suggestions"]["clarification_count"], 2)
        with self.assertRaises(ValueError):
            self.agent.respond("s", "again", 11, 10)

    def test_idempotency_and_invalid_order(self):
        first = self.respond("I need a blue dress under $50.")
        self.assertEqual(self.agent.respond("s", "I need a blue dress under $50.", 1, 10), first)
        self.assertEqual(self.agent.memory.versions["s"], 2)
        with self.assertRaises(ValueError):
            self.agent.respond("s", "other input", 1, 10)
        with self.assertRaises(ValueError):
            self.agent.respond("s", "other input", 3, 10)

    def test_session_isolation(self):
        self.respond("I need a blue dress under $50.")
        self.agent.reset("other", {})
        out = self.agent.respond("other", "Hello", 1)
        self.assertEqual(out["ask_attribute"], "category")
        self.assertEqual(self.agent.memory.sessions["s"].hard_slots["color"].value, "blue")

    def test_exclusion_can_be_removed_without_setting_positive_slot(self):
        self.respond("I need a dress, not black.")
        self.respond("Black is also fine.", 2)
        state = self.event("3_state")
        self.assertEqual(state["exclusions"], {})
        self.assertNotIn("color", state["hard_constraints"])

    def test_disclosed_feature_does_not_switch_category(self):
        self.respond("I need a dress.")
        self.respond("For that, what matters is: a gift for kids inspired by Goddess.", 2)
        state = self.event("3_state")
        self.assertEqual(state["hard_constraints"], {"category": "dress"})
        self.assertTrue(any(k.startswith("feature_") for k in state["soft_preferences"]))
        self.assertNotIn("brand", state["soft_preferences"])

    def test_failure_does_not_recommend_stale_candidates(self):
        self.respond("I need a black dress.")
        with patch.object(self.agent.reranker, "rerank", side_effect=RuntimeError("test failure")):
            result = self.agent.respond("s", "Blue instead.", 2, 10)
        self.assertEqual(result["recommendations"], [])
        self.assertEqual(self.agent.errors[-1]["stage"], "4B_ranking")
        self.assertEqual(self.event("3_feedback")["hard_constraints"]["color"], "blue")

    def test_cross_session_result_rejected(self):
        self.respond("I need a dress.")
        state = self.agent.memory.snapshot("s")
        result = self.agent.retriever.generate(RetrievalRequest.from_state(state))
        with self.assertRaises(ValueError):
            result.validate_context(session_id="wrong", turn=state.turn, state_version=state.state_version)

    def test_soft_retry_is_bounded_and_does_not_drop_hard_constraints(self):
        self.respond("I need a purple dress, preferably cotton.")
        events = self.agent.trace[-1]["events"]
        self.assertEqual(sum(e["stage"] == "2_retrieval_retry" for e in events), 1)
        retry = self.event("5_retry_plan")
        self.assertTrue(retry["relax_soft"])
        self.assertEqual(retry["state"]["hard_constraints"]["color"], "purple")

    def test_router_initial_natural_phrase_not_whole_category(self):
        parsed = TurnIntentRouter().understand_turn("I'm looking for a black dress under $50.")
        self.assertEqual(next(op.values[0] for op in parsed.slot_updates if op.slot == "category"), "dress")

    def test_result_control_reply_does_not_overwrite_pending_category(self):
        router = TurnIntentRouter()
        for text in ("Show me the strongest matches.", "Show me the results.", "Recommend the best options."):
            parsed = router.understand_turn(text, pending_question={"target_slot": "category", "constraint_type": "hard"})
            self.assertFalse(any(op.slot == "category" for op in parsed.slot_updates), text)
        parsed = router.understand_turn("Running shoes.", pending_question={"target_slot": "category", "constraint_type": "hard"})
        self.assertTrue(any(op.slot == "category" and op.values == ("shoes",) for op in parsed.slot_updates))

    def test_guided_jersey_story_keeps_category_on_result_request(self):
        self.agent.reset("story", {"preference_tags": ["comfort"]})
        self.agent.respond("story", "I need a blue basketball jersey under $60.", 1, 10)
        self.agent.respond("story", "Breathable mesh and a drawstring matter.", 2, 10)
        result = self.agent.respond("story", "Show me the strongest matches.", 3, 10)
        state = next(
            event["output"]
            for event in self.agent.trace[-1]["events"]
            if event["stage"] == "3_state"
        )
        self.assertEqual(state["hard_constraints"]["category"], "jersey")
        self.assertIn("basketball", [row["value"] for row in state["soft_preferences"]["use_case"]])
        self.assertEqual(result["recommendations"][0]["parent_asin"], "JERSEY")
        self.assertNotIn("PAJAMA", [row["parent_asin"] for row in result["recommendations"]])

    def test_short_answers_and_structured_override_reach_state(self):
        router = TurnIntentRouter()
        feature = router.understand_turn("Drawstring closure.", pending_question={"target_slot": "feature", "constraint_type": "soft"})
        self.assertTrue(any(op.slot.startswith("feature_") and op.operation == "set" for op in feature.slot_updates))
        budget = router.understand_turn("$50", pending_question={"target_slot": "budget", "constraint_type": "hard"})
        self.assertTrue(any(op.slot == "price_max" and op.values == (50.0,) for op in budget.slot_updates))
        floor = router.understand_turn("My budget is at least $100.")
        self.assertTrue(any(op.slot == "price_min" and op.values == (100.0,) for op in floor.slot_updates))
        self.respond("I'm looking for dresses. Cotton.")
        self.respond("Actually, what I need is: linen.", 2)
        state = self.event("3_state")
        self.assertEqual(state["hard_constraints"]["material"], "linen")
        self.assertNotIn("cotton", json.dumps(state["hard_constraints"]))

    def test_catalog_store_words_are_not_accidental_brands(self):
        self.agent.router.known_brands.update({"switch", "not", "need"})
        self.respond("Switch to shoes, not leather.")
        self.assertEqual(self.event("3_state")["hard_constraints"], {"category": "shoes"})

    def test_explicit_brand_is_passed_to_filter(self):
        self.respond("I need a dress from Acme.")
        self.assertEqual(self.event("3_state")["hard_constraints"]["brand"], "acme")

    def test_official_preference_override_does_not_leave_stale_hard_material(self):
        self.respond("I'm looking for Shoes. Material: leather.")
        self.respond("Actually, ignore my earlier preference. What I need is: canvas.", 2)
        state = self.event("3_state")
        self.assertNotIn("leather", json.dumps(state["hard_constraints"]))
        self.assertEqual(state["soft_preferences"], {})
        self.assertIn("canvas", json.dumps(state["hard_constraints"]))

    def test_post_policy_can_clarify_broad_pool(self):
        self.respond("I need a dress.")
        state = self.agent.memory.snapshot("s")
        result = self.agent.retriever.generate(RetrievalRequest.from_state(state))
        result = replace(result, stats=replace(result.stats, matched_count=150, filtered_count=150))
        ranking = self.agent.reranker.rerank(result, top_k=10)
        decision = self.agent.post_policy.decide(state, result, ranking)
        self.assertEqual(decision.action, "clarify")
        self.assertEqual(decision.question["ask_attribute"], "feature")
        self.assertEqual(decision.question["target_slot"], "style")

    def test_score_compat_policy_is_state_based_and_configuration_is_explicit(self):
        self.respond("I need a blue dress under $50.")
        state = self.agent.memory.snapshot("s")
        decision = PreRetrievalPolicy(minimum_evidence=4).decide(state)
        self.assertEqual(decision.action, "clarify")
        self.assertEqual(decision.question["target_slot"], "other")
        score_agent = FinalAgent(self.catalog, orchestration_mode="score_compat")
        self.assertEqual(score_agent.retriever.mode, "recall_compat")
        self.assertEqual(score_agent.reranker.mode, "locked")

    def test_clarification_ablation_profiles_expose_distinct_question_budgets(self):
        state_based = FinalAgent(self.catalog, orchestration_mode="score_compat")
        fixed = FinalAgent(self.catalog, orchestration_mode="score_compat", clarification_mode="fixed_two_dynamic")
        value_based = FinalAgent(self.catalog, orchestration_mode="score_compat", clarification_mode="one_then_value")
        self.assertEqual((state_based.pre_policy.minimum_questions, state_based.pre_policy.minimum_evidence,
                          state_based.post_policy.max_questions), (0, 4, 2))
        self.assertEqual((fixed.pre_policy.minimum_questions, fixed.pre_policy.minimum_evidence,
                          fixed.post_policy.max_questions), (2, 0, 3))
        self.assertEqual((value_based.pre_policy.minimum_questions, value_based.pre_policy.minimum_evidence,
                          value_based.post_policy.max_questions), (1, 4, 3))

    def test_profile_is_soft_and_decay_does_not_touch_hard(self):
        self.respond("I need a blue dress, ideally cotton.")
        before = self.event("3_state")
        self.assertEqual(before["hard_constraints"]["color"], "blue")
        self.respond("Thanks.", 2)
        after = self.event("3_state")
        self.assertEqual(after["hard_constraints"], before["hard_constraints"])
        self.assertLess(after["soft_preferences"]["material"][0]["weight"], before["soft_preferences"]["material"][0]["weight"])
        self.assertEqual(after["profile_hints"]["preference_tags"], ["comfort"])

    def test_negative_feedback_gets_a_new_question_without_repeating_feature(self):
        self.respond("I need a dress.")
        first = self.respond("Those options are not quite right yet.", 2)
        self.assertEqual(first["ask_attribute"], "feature")
        self.respond("For that, what matters is: cotton.", 3)
        second = self.respond("Those options are not quite right yet.", 4)
        self.assertEqual(second["ask_attribute"], "other")
        self.assertEqual(self.event("3_feedback")["suggestions"]["clarification_count"], 2)

    def test_dense_runtime_failure_keeps_lexical_candidates_and_warning(self):
        backend = self.agent.retriever.backend
        with patch.object(backend, "_dense_ranking", side_effect=RuntimeError("model offline"), create=True):
            result = self.respond("I need a black dress under $50.")
        self.assertEqual(result["recommendations"][0]["parent_asin"], "BLACK")
        self.assertTrue(any("Dense failed" in warning for warning in self.event("2_retrieval")["warnings"]))


if __name__ == "__main__":
    unittest.main()
