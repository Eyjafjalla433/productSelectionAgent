import json
import threading
import unittest
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from mvp.audit import GENESIS_DIGEST, chain_digest, verify_audit
from mvp.control_intent import parse_control_intent
from mvp.explanations import explain_product, product_advice, summarize_explanations
from mvp.localization import localized_agent_message, message_locale
from mvp.server import AgentRuntime, ApiError, build_receipt, create_server
from mvp.shadow_policy import option_prompt, shadow_question_board
from shopping_agent.comparison_enhancer import ComparisonOutcome


class FakeRetriever:
    products = {
        "A1": {
            "parent_asin": "A1",
            "title": "Blue cotton shirt",
            "store": "Demo",
            "price": 24.5,
            "average_rating": 4.6,
            "rating_number": 80,
            "categories": ["Clothing", "Shirts"],
            "features": ["Cotton", "Machine wash"],
            "description": ["A lightweight blue cotton shirt for warm days."],
            "details": {"Care": "Machine wash"},
        }
    }


class FakeAgent:
    def __init__(self):
        self.trace = []
        self.retriever = FakeRetriever()
        self.sessions = set()
        self.rejected = {}
        self.state_versions = {}

    @property
    def catalog_size(self):
        return len(self.retriever.products)

    def get_catalog_product(self, parent_asin):
        product = self.retriever.products.get(parent_asin)
        return dict(product) if product is not None else None

    def reset(self, session_id, profile):
        self.sessions.add(session_id)
        self.rejected[session_id] = []
        self.state_versions[session_id] = 1

    def drop_session(self, session_id):
        self.sessions.discard(session_id)
        self.rejected.pop(session_id, None)
        self.state_versions.pop(session_id, None)

    def record_product_feedback(self, session_id, *, source_turn, rejected_asins=(), liked_asins=()):
        values = self.rejected[session_id]
        for asin in liked_asins:
            if asin in values:
                values.remove(asin)
        for asin in rejected_asins:
            if asin not in values:
                values.append(asin)
        self.state_versions[session_id] += 1
        payload = {"state_version": self.state_versions[session_id], "rejected_asins": tuple(values)}
        return SimpleNamespace(to_dict=lambda: payload)

    def respond(self, session_id, message, turn, top_k):
        self.trace.append(
            {
                "session_id": session_id,
                "turn": turn,
                "events": [
                    {"stage": "3_state", "elapsed_ms": 1.0, "output": {"state_version": 1, "hard_constraints": {"color": "blue"}, "soft_preferences": {}, "exclusions": {}, "slot_metadata": {"hard": {"color": {"source_turn": turn, "evidence": message}}, "soft": {}}, "suggestions": {}}},
                    {"stage": "4A_pre_policy", "elapsed_ms": 1.2, "output": {"action": "retrieve", "reason": "enough_evidence"}},
                    {"stage": "2_retrieval", "elapsed_ms": 2.0, "output": {"returned_count": 2, "stats": {}, "candidates": [
                        {"product": {"title": "Blue cotton shirt", "store": "Demo", "price": 24.5, "categories": ["Clothing", "Shirts"], "features": ["Cotton", "Blue"]}},
                        {"product": {"title": "Red wool coat", "store": "Other", "price": 80, "categories": ["Clothing", "Coats"], "features": ["Wool", "Red"]}},
                    ]}},
                    {"stage": "4B_ranking", "elapsed_ms": 2.4, "output": {"ranking_method": "fake", "ranked_candidates": [{"parent_asin": "A1", "evidence": ["color"]}]}},
                    {"stage": "4B_post_policy", "elapsed_ms": 2.5, "output": {"action": "recommend", "reason": "ranked"}},
                    {"stage": "3_feedback", "elapsed_ms": 2.6, "output": {"shown_asins": ["A1"], "suggestions": {"clarification_count": 0}}},
                ],
            }
        )
        return {"message": "One match", "ask_attribute": None, "recommendations": [{"parent_asin": "A1", "score": 0.9}], "usage": {"prompt_tokens": 0, "completion_tokens": 0}}

    def record_control(self, session_id, message, turn, response, control, top_k):
        self.trace.append(
            {
                "session_id": session_id,
                "turn": turn,
                "user_message": message,
                "events": [
                    {"stage": "0_control", "elapsed_ms": 0.0, "output": control},
                    {"stage": "response", "elapsed_ms": 0.0, "output": response},
                ],
            }
        )
        return {
            "message": response["message"],
            "ask_attribute": None,
            "recommendations": [],
            "usage": response.get("usage", {"prompt_tokens": 0, "completion_tokens": 0}),
        }


class SwitchingFakeAgent(FakeAgent):
    def respond(self, session_id, message, turn, top_k):
        response = super().respond(session_id, message, turn, top_k)
        state = self.trace[-1]["events"][0]["output"]
        state["hard_constraints"]["category"] = "shirts" if turn == 1 else "shoes"
        return response


class RequirementChangingFakeAgent(FakeAgent):
    def respond(self, session_id, message, turn, top_k):
        response = super().respond(session_id, message, turn, top_k)
        state = self.trace[-1]["events"][0]["output"]
        state["hard_constraints"]["color"] = "blue" if turn == 1 else "red"
        state["state_version"] = turn
        return response


class ModelAssistedFakeAgent(FakeAgent):
    def respond(self, session_id, message, turn, top_k):
        response = super().respond(session_id, message, turn, top_k)
        self.trace[-1]["events"].insert(
            0,
            {
                "stage": "1_model_enhancement",
                "elapsed_ms": 0.8,
                "output": {
                    "updates": [{"slot": "style", "operation": "set", "values": ["relaxed"]}],
                    "provider": "local",
                    "model": "test-model",
                    "usage": {"prompt_tokens": 21, "completion_tokens": 7},
                    "latency_ms": 4.2,
                    "warning": None,
                },
            },
        )
        response["usage"] = {"prompt_tokens": 21, "completion_tokens": 7}
        return response


class StaticComparisonEnhancer:
    def __init__(self):
        self.calls = 0

    def compare(self, handoff):
        self.calls += 1
        asin = handoff["selected_products"][0]["parent_asin"]
        return ComparisonOutcome(
            ({"parent_asin": asin, "pros": [{"text": "Easy care", "evidence": "Machine wash", "source": "model_catalog_quote"}], "cons": []},),
            "fake",
            "fake-comparison",
            {"prompt_tokens": 20, "completion_tokens": 9},
            3.5,
        )


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.runtime = AgentRuntime(FakeAgent())
        self.session_id = self.runtime.new_session()["session_id"]

    def test_chat_enriches_products_and_receipt(self):
        result = self.runtime.chat(self.session_id, "A blue shirt")
        self.assertEqual(result["turn"], 1)
        self.assertEqual(result["products"][0]["title"], "Blue cotton shirt")
        self.assertEqual(result["products"][0]["evidence"], ["color"])
        self.assertEqual(result["receipt"]["hard"], {"color": "blue"})
        self.assertEqual(result["receipt"]["candidate_count"], 2)
        self.assertTrue(result["receipt"]["shadow_questions"])
        self.assertEqual(result["receipt"]["repeat_count"], 0)
        self.assertEqual(result["products"][0]["match"]["hard_coverage"], 1.0)
        self.assertEqual(result["receipt"]["result_quality"]["fully_supported_count"], 1)
        self.assertEqual(result["receipt"]["novelty_mode"], "not_applied")
        self.assertEqual(result["receipt"]["state_evidence"]["hard"]["color"]["source_turn"], 1)
        self.assertIn({"kind": "added", "group": "hard", "slot": "color", "value": "blue"}, result["receipt"]["state_changes"])
        repeated = self.runtime.chat(self.session_id, "Show that one again")
        self.assertEqual(repeated["receipt"]["new_product_count"], 0)
        self.assertEqual(repeated["receipt"]["repeat_count"], 1)

    def test_category_switch_starts_new_novelty_scope(self):
        runtime = AgentRuntime(SwitchingFakeAgent())
        session_id = runtime.new_session()["session_id"]
        first = runtime.chat(session_id, "A shirt")
        runtime.update_selection(session_id, parent_asin="A1", selected=True)
        second = runtime.chat(session_id, "Switch to shoes")
        self.assertEqual(first["receipt"]["intent_version"], 1)
        self.assertEqual(second["receipt"]["intent_version"], 2)
        self.assertTrue(second["receipt"]["intent_reset"])
        self.assertTrue(second["receipt"]["selection_reset"])
        self.assertEqual(runtime.selection_handoff(session_id)["status"], "draft")
        self.assertEqual(second["receipt"]["repeat_count"], 0)
        self.assertIn(
            {"kind": "updated", "group": "hard", "slot": "category", "previous": "shirts", "value": "shoes"},
            second["receipt"]["state_changes"],
        )

    def test_rejects_unknown_session_and_empty_message(self):
        with self.assertRaises(ApiError):
            self.runtime.chat("missing", "hello")
        with self.assertRaises(ApiError):
            self.runtime.chat(self.session_id, "   ")
        with self.assertRaisesRegex(ApiError, "1,000 characters or fewer") as error:
            self.runtime.chat(self.session_id, "x" * 1001)
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(error.exception.code, "message_too_long")
        self.assertEqual(self.runtime.sessions[self.session_id].next_turn, 1)
        self.assertEqual(self.runtime.agent.trace, [])

    def test_session_ttl_and_capacity_release_agent_state(self):
        runtime = AgentRuntime(FakeAgent(), session_ttl_seconds=10, max_sessions=2)
        first = runtime.new_session()["session_id"]
        second = runtime.new_session()["session_id"]
        runtime.sessions[first].last_access -= 20
        third = runtime.new_session()["session_id"]
        self.assertNotIn(first, runtime.sessions)
        self.assertNotIn(first, runtime.agent.sessions)
        self.assertEqual(set(runtime.sessions), {second, third})
        health = runtime.health()
        self.assertEqual(health["schema_version"], "show-me-your-agent.health.v1")
        self.assertEqual(health["sessions"], 2)
        self.assertEqual(health["max_sessions"], 2)
        self.assertEqual(health["session_ttl_seconds"], 10.0)
        self.assertEqual(health["concurrency_mode"], "single_threaded_sqlite_owner")
        self.assertEqual(health["catalog_products"], 1)
        with self.assertRaises(ApiError):
            runtime.chat(first, "hello")

        runtime.sessions[second].last_access -= 20
        with self.assertRaisesRegex(ApiError, "expired"):
            runtime.audit(second)
        self.assertNotIn(second, runtime.agent.sessions)

        with self.assertRaises(ValueError):
            AgentRuntime(FakeAgent(), session_ttl_seconds=0)
        with self.assertRaises(ValueError):
            AgentRuntime(FakeAgent(), max_sessions=0)

    def test_receipt_does_not_include_full_candidates(self):
        self.runtime.chat(self.session_id, "A blue shirt")
        receipt = build_receipt(self.runtime.agent.trace[-1])
        self.assertNotIn("ranked_candidates", receipt)
        self.assertEqual(receipt["top_evidence"], ["color"])

    def test_optional_model_usage_is_visible_and_bounded(self):
        runtime = AgentRuntime(ModelAssistedFakeAgent(), model_provider="local")
        session = runtime.new_session()
        self.assertEqual(session["schema_version"], "show-me-your-agent.session.v1")
        self.assertEqual(session["catalog_products"], 1)
        self.assertEqual(session["model_provider"], "local")
        self.assertFalse(session["cloud_model"])
        self.assertEqual(session["data_boundary"], "configured_local_endpoint")
        result = runtime.chat(session["session_id"], "A relaxed blue shirt")
        assist = result["receipt"]["model_assist"]
        self.assertEqual(assist["provider"], "local")
        self.assertEqual(assist["model"], "test-model")
        self.assertEqual(result["receipt"]["model_usage"], {"prompt_tokens": 21, "completion_tokens": 7})
        self.assertNotIn("api_key", str(result))

    def test_remote_model_data_boundary_is_explicit_in_session_health_and_audit(self):
        runtime = AgentRuntime(
            FakeAgent(),
            model_provider="local",
            model_name="remote-test",
            model_cloud=True,
        )
        session = runtime.new_session()
        self.assertTrue(session["cloud_model"])
        self.assertEqual(session["data_boundary"], "configured_external_provider")
        self.assertIn("may receive shopper messages", session["data_disclosure"])
        self.assertTrue(runtime.health()["cloud_model"])
        audit = runtime.audit(session["session_id"])
        self.assertTrue(audit["model"]["cloud_model"])
        self.assertEqual(audit["model"]["name"], "remote-test")
        self.assertIn("shopper messages", audit["privacy_note"])

    def test_selection_is_server_validated_and_handoff_is_structured(self):
        empty = self.runtime.selection_handoff(self.session_id)
        self.assertEqual(empty["status"], "draft")
        with self.assertRaises(ApiError):
            self.runtime.update_selection(self.session_id, parent_asin="A1", selected=True)

        self.runtime.chat(self.session_id, "A blue shirt")
        selected = self.runtime.update_selection(
            self.session_id,
            parent_asin="A1",
            selected=True,
            reason="Best hard-constraint coverage",
        )
        self.assertEqual(selected["selected_asins"], ["A1"])
        self.assertEqual(selected["schema_version"], "show-me-your-agent.selection-state.v1")
        self.assertEqual(selected["status"], "ready_for_comparison")
        self.assertFalse(selected["finalized"])
        handoff = self.runtime.selection_handoff(self.session_id)
        self.assertEqual(handoff["schema_version"], "show-me-your-agent.selection.v1")
        self.assertEqual(handoff["status"], "ready_for_comparison")
        self.assertEqual(handoff["requirements"]["hard"], {"color": "blue"})
        product = handoff["selected_products"][0]
        self.assertEqual(product["parent_asin"], "A1")
        self.assertEqual(product["product_description"], ["A lightweight blue cotton shirt for warm days."])
        self.assertEqual(product["product_bullet_points"], ["Cotton", "Machine wash"])
        self.assertEqual(product["selection"]["reason"], "Best hard-constraint coverage")
        self.assertTrue(product["comparison_evidence"]["strengths"])
        self.assertTrue(product["advice"]["pros"])
        self.assertTrue(product["advice"]["cons"])
        self.assertEqual(handoff["comparison_contract"]["owner"], "B")
        self.assertEqual(handoff["comparison_contract"]["consumer"], "D")
        self.assertEqual(handoff["comparison_summary"]["lowest_price_asin"], "A1")
        self.assertEqual(handoff["comparison_summary"]["highest_rating_asin"], "A1")
        self.assertEqual(handoff["comparison_summary"]["rows"][0]["hard_coverage"], 1.0)

        cleared = self.runtime.update_selection(self.session_id, clear=True)
        self.assertEqual(cleared["selection_count"], 0)

    def test_product_detail_is_bounded_to_shown_products_and_model_free(self):
        with self.assertRaises(ApiError) as hidden:
            self.runtime.product_detail(self.session_id, "A1")
        self.assertEqual(hidden.exception.code, "product_not_shown")

        self.runtime.chat(self.session_id, "A blue shirt")
        trace_count = len(self.runtime.agent.trace)
        next_turn = self.runtime.sessions[self.session_id].next_turn
        detail = self.runtime.product_detail(self.session_id, "A1")
        self.assertEqual(detail["schema_version"], "show-me-your-agent.product-detail.v1")
        self.assertEqual(detail["product_description"], ["A lightweight blue cotton shirt for warm days."])
        self.assertEqual(detail["product_bullet_points"], ["Cotton", "Machine wash"])
        self.assertTrue(detail["requirement_match"]["signals"])
        self.assertTrue(detail["advice"]["pros"])
        self.assertIn("no retrieval, reranking or model call", detail["source_note"])
        self.assertEqual(len(self.runtime.agent.trace), trace_count)
        self.assertEqual(self.runtime.sessions[self.session_id].next_turn, next_turn)

    def test_audit_is_bounded_and_detached(self):
        self.runtime.chat(self.session_id, "A blue shirt")
        self.runtime.chat(self.session_id, "Keep the same constraints")
        audit = self.runtime.audit(self.session_id)
        self.assertEqual(audit["schema_version"], "show-me-your-agent.audit.v1")
        self.assertEqual(audit["turn_count"], 2)
        self.assertEqual(audit["turns"][0]["user_message"], "A blue shirt")
        self.assertNotIn("ranked_candidates", str(audit))
        previous = GENESIS_DIGEST
        for stored_turn in audit["turns"]:
            turn = dict(stored_turn)
            integrity = turn.pop("integrity")
            self.assertEqual(integrity["previous_sha256"], previous)
            self.assertEqual(chain_digest(previous, turn), integrity["sha256"])
            previous = integrity["sha256"]
        self.assertEqual(audit["integrity"]["head"], previous)
        self.assertFalse(audit["integrity"]["signed"])
        audit["turns"][0]["user_message"] = "mutated"
        self.assertEqual(self.runtime.audit(self.session_id)["turns"][0]["user_message"], "A blue shirt")

    def test_audit_verifier_detects_tampering_and_reordering(self):
        self.runtime.chat(self.session_id, "A blue shirt")
        self.runtime.chat(self.session_id, "Keep the same constraints")
        audit = self.runtime.audit(self.session_id)
        self.assertEqual(verify_audit(audit), [])

        tampered = self.runtime.audit(self.session_id)
        tampered["turns"][0]["user_message"] = "A red shirt"
        self.assertTrue(any("digest" in error for error in verify_audit(tampered)))

        reordered = self.runtime.audit(self.session_id)
        reordered["turns"].reverse()
        errors = verify_audit(reordered)
        self.assertTrue(any("turn number" in error or "preceding digest" in error for error in errors))

    def test_audit_rejects_unknown_session(self):
        with self.assertRaises(ApiError):
            self.runtime.audit("missing")

    def test_shadow_board_penalizes_unoffered_remainder(self):
        products = [
            {"store": f"brand-{index}", "categories": ["Clothing", "Shirts" if index < 3 else "Coats"], "price": 20 + index * 20}
            for index in range(6)
        ]
        board = shadow_question_board(products, turns_left=5, max_options=2)
        by_name = {row["attribute"]: row for row in board}
        self.assertLess(by_name["brand"]["coverage"], 0.5)
        self.assertGreater(by_name["budget"]["coverage"], by_name["brand"]["coverage"])
        self.assertEqual(option_prompt("budget", "$100+"), "My budget is at least $100.")
        self.assertEqual(option_prompt("budget", "$25-49"), "My budget is between $25 and $49.")

    def test_chat_control_parser_is_conservative_and_multilingual(self):
        self.assertEqual(parse_control_intent("Compare #1 and #3").action, "compare")
        self.assertEqual(parse_control_intent("Compare #1 and #3").ranks, (1, 3))
        self.assertEqual(parse_control_intent("选择第一个和第三个").ranks, (1, 3))
        self.assertEqual(parse_control_intent("remove the second one").action, "remove")
        self.assertEqual(parse_control_intent("清空候选清单").action, "clear")
        self.assertEqual(parse_control_intent("不要第一个").action, "reject")
        self.assertEqual(parse_control_intent("I don't like #2").action, "reject")
        self.assertEqual(parse_control_intent("Finalize my selection").action, "finalize")
        self.assertEqual(parse_control_intent("完成选择").action, "finalize")
        self.assertEqual(parse_control_intent("Export selection").action, "handoff")
        self.assertIsNone(parse_control_intent("I need a clear blue phone case"))

    def test_chinese_input_keeps_english_output(self):
        result = self.runtime.chat(self.session_id, "我想要一件蓝色衬衫")
        self.assertNotRegex(result["assistant"]["message"], r'[\u3400-\u9fff]')
        compared = self.runtime.chat(self.session_id, "比较第一个")
        self.assertNotRegex(compared["assistant"]["message"], r'[\u3400-\u9fff]')
        self.assertEqual(self.runtime.sessions[self.session_id].locale, "en")
        self.assertEqual(message_locale("Show me more", "zh"), "en")

    def test_explicit_product_rejection_is_recorded_without_requirement_mutation(self):
        self.runtime.chat(self.session_id, "我想要一件蓝色衬衫")
        rejected = self.runtime.chat(self.session_id, "不要第一个")
        self.assertEqual(rejected["receipt"]["pre_action"], "control")
        self.assertEqual(rejected["receipt"]["state_changes"], [])
        self.assertEqual(rejected["selection_state"]["rejected_asins"], ["A1"])
        self.assertEqual(rejected["receipt"]["rejected_asins"], ["A1"])
        self.assertIn("stay out", rejected["assistant"]["message"])

    def test_chinese_error_message_does_not_claim_empty_catalog(self):
        message = localized_agent_message(
            locale="zh",
            fallback="failed",
            ask_attribute=None,
            receipt={"timings": [{"stage": "error", "elapsed_ms": 1}]},
            product_count=0,
        )
        self.assertIn("搜索出了点问题", message)

    def test_chat_selection_control_does_not_retrieve_or_mutate_state(self):
        first = self.runtime.chat(self.session_id, "A blue shirt")
        trace_count = len(self.runtime.agent.trace)
        compared = self.runtime.chat(self.session_id, "Compare #1")
        self.assertEqual(compared["turn"], 2)
        self.assertEqual(compared["receipt"]["pre_action"], "control")
        self.assertEqual(compared["receipt"]["state_changes"], [])
        self.assertEqual(compared["selection_state"]["selected_asins"], ["A1"])
        self.assertEqual(compared["handoff"]["status"], "ready_for_comparison")
        self.assertEqual(compared["products"], first["products"])
        self.assertEqual(len(self.runtime.agent.trace), trace_count + 1)
        self.assertEqual(self.runtime.agent.trace[-1]["events"][0]["stage"], "0_control")

        removed = self.runtime.chat(self.session_id, "Remove the first one")
        self.assertEqual(removed["turn"], 3)
        self.assertEqual(removed["selection_state"]["selection_count"], 0)
        audit = self.runtime.audit(self.session_id)
        self.assertEqual(audit["turn_count"], 3)
        self.assertEqual(verify_audit(audit), [])

    def test_compare_before_results_is_truthful_and_model_free(self):
        enhancer = StaticComparisonEnhancer()
        runtime = AgentRuntime(FakeAgent(), model_provider="fake", comparison_enhancer=enhancer)
        session_id = runtime.new_session()["session_id"]
        result = runtime.chat(session_id, "Compare #1")
        self.assertIn("Select at least one shown product", result["assistant"]["message"])
        self.assertEqual(result["handoff"]["status"], "draft")
        self.assertEqual(result["selection_state"]["selection_count"], 0)
        self.assertEqual(result["assistant"]["usage"], {"prompt_tokens": 0, "completion_tokens": 0})
        self.assertEqual(enhancer.calls, 0)

    def test_comparison_model_runs_only_on_explicit_compare_and_is_cached(self):
        enhancer = StaticComparisonEnhancer()
        runtime = AgentRuntime(FakeAgent(), model_provider="fake", comparison_enhancer=enhancer)
        session_id = runtime.new_session()["session_id"]
        first = runtime.chat(session_id, "A blue shirt")
        self.assertEqual(first["assistant"]["usage"], {"prompt_tokens": 0, "completion_tokens": 0})
        self.assertEqual(enhancer.calls, 0)

        compared = runtime.chat(session_id, "Compare #1")
        self.assertEqual(enhancer.calls, 1)
        self.assertEqual(compared["assistant"]["usage"], {"prompt_tokens": 20, "completion_tokens": 9})
        self.assertFalse(compared["handoff"]["comparison_assist"]["cached"])
        self.assertEqual(compared["receipt"]["model_usage"], {"prompt_tokens": 20, "completion_tokens": 9})

        repeated = runtime.chat(session_id, "Compare #1")
        self.assertEqual(enhancer.calls, 1)
        self.assertTrue(repeated["handoff"]["comparison_assist"]["cached"])
        self.assertEqual(repeated["assistant"]["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

        cached = runtime.selection_handoff(session_id)
        self.assertEqual(enhancer.calls, 1)
        self.assertTrue(cached["comparison_assist"]["cached"])
        self.assertEqual(cached["comparison_assist"]["usage_this_call"], {"prompt_tokens": 0, "completion_tokens": 0})

        finalized = runtime.chat(session_id, "Finalize my selection")
        self.assertEqual(finalized["schema_version"], "show-me-your-agent.chat.v1")
        self.assertEqual(enhancer.calls, 1)
        self.assertTrue(finalized["handoff"]["comparison_assist"]["cached"])
        self.assertEqual(finalized["assistant"]["usage"], {"prompt_tokens": 0, "completion_tokens": 0})
        self.assertEqual(finalized["handoff"]["status"], "finalized")
        self.assertTrue(finalized["handoff"]["decision"]["finalized"])
        self.assertEqual(finalized["selection_state"]["status"], "finalized")
        self.assertTrue(finalized["selection_state"]["finalized"])
        self.assertEqual(finalized["handoff"]["decision"]["finalized_turn"], finalized["turn"])
        self.assertTrue(finalized["handoff"]["decision"]["finalized_at_utc"])
        audit = runtime.audit(session_id)
        self.assertTrue(audit["turns"][-1]["selection_decision"]["finalized"])
        self.assertEqual(verify_audit(audit), [])

        selection_state = runtime.update_selection(session_id, parent_asin="A1", selected=False)
        self.assertEqual(selection_state["status"], "draft")
        self.assertFalse(selection_state["finalized"])
        invalidated = runtime.selection_handoff(session_id)
        self.assertEqual(invalidated["status"], "draft")
        self.assertFalse(invalidated["decision"]["finalized"])

    def test_same_category_requirement_change_reopens_finalized_selection(self):
        runtime = AgentRuntime(RequirementChangingFakeAgent())
        session_id = runtime.new_session()["session_id"]
        runtime.chat(session_id, "A blue shirt")
        runtime.update_selection(session_id, parent_asin="A1", selected=True)
        finalized = runtime.chat(session_id, "Finalize my selection")
        self.assertEqual(finalized["selection_state"]["status"], "finalized")

        changed = runtime.chat(session_id, "Make it red")
        self.assertTrue(changed["receipt"]["selection_finalization_invalidated"])
        self.assertIn(
            {"kind": "updated", "group": "hard", "slot": "color", "previous": "blue", "value": "red"},
            changed["receipt"]["state_changes"],
        )
        self.assertEqual(changed["selection_state"]["status"], "ready_for_comparison")
        self.assertEqual(changed["selection_state"]["selected_asins"], ["A1"])
        handoff = runtime.selection_handoff(session_id)
        self.assertFalse(handoff["decision"]["finalized"])
        self.assertEqual(verify_audit(runtime.audit(session_id)), [])

    def test_match_explanation_distinguishes_unknown_and_conflict(self):
        receipt = {"hard": {"category": "shirt", "color": "blue", "price_max": 20}, "soft": {"material": ["cotton"]}, "excluded": {"style": ["formal"]}}
        unknown = explain_product({"title": "blue cotton shirt", "categories": ["Shirts"], "price": None}, receipt)
        statuses = {signal["slot"]: signal["status"] for signal in unknown["signals"]}
        self.assertEqual(statuses["category"], "supported")
        self.assertEqual(statuses["color"], "supported")
        self.assertEqual(statuses["price_max"], "unknown")
        self.assertEqual(statuses["material"], "supported")

        conflict = explain_product({"title": "formal blue cotton shirt", "categories": ["Shirts"], "price": 30}, receipt)
        statuses = {(signal["tier"], signal["slot"]): signal["status"] for signal in conflict["signals"]}
        self.assertEqual(statuses[("hard", "price_max")], "conflict")
        self.assertEqual(statuses[("excluded", "style")], "conflict")
        summary = summarize_explanations([unknown, conflict])
        self.assertEqual(summary["fully_supported_count"], 0)

        dress_shirt = explain_product(
            {"title": "Blue cotton dress shirt", "categories": ["Shirts", "Dress Shirts"], "price": 20},
            {"hard": {"category": "dress"}, "soft": {}, "excluded": {}},
        )
        category_signal = next(signal for signal in dress_shirt["signals"] if signal["slot"] == "category")
        self.assertEqual(category_signal["status"], "conflict")
        self.assertEqual(dress_shirt["hard_coverage"], 0.0)

    def test_feature_metadata_prefix_uses_same_evidence_terms_as_retrieval(self):
        product = {
            "parent_asin": "ALLOY",
            "title": "Moon pendant necklace",
            "categories": ["Jewelry", "Necklaces"],
            "features": ["Durable alloy pendant"],
            "description": [],
            "details": {},
            "store": "Demo",
            "price": 20,
        }
        receipt = {
            "hard": {
                "category": "jewelry necklaces",
                "feature_material": "material:alloy",
            },
            "soft": {},
            "excluded": {},
        }
        match = explain_product(product, receipt)
        feature = next(row for row in match["signals"] if row["slot"] == "feature_material")
        self.assertEqual(feature["status"], "supported")
        advice = product_advice(product, match)
        point = next(row for row in advice["pros"] if "feature_material" in row["text"])
        self.assertEqual(point["source"], "bullet_point")
        self.assertIn("alloy", point["evidence"].lower())

    def test_product_advice_is_source_labelled_and_surfaces_unknowns(self):
        product = {
            "title": "Blue cotton shirt",
            "features": ["Soft cotton fabric for warm days"],
            "description": ["A lightweight blue shirt."],
            "categories": ["Shirts"],
            "price": None,
            "average_rating": 4.5,
            "rating_number": 120,
        }
        match = explain_product(product, {"hard": {"color": "blue", "size": "XL"}, "soft": {"material": ["cotton"]}, "excluded": {}})
        advice = product_advice(product, match)
        self.assertTrue(any(row["source"] in {"description", "bullet_point"} for row in advice["pros"]))
        self.assertTrue(any("Could not verify size" in row["text"] for row in advice["cons"]))
        self.assertTrue(any(row["evidence"] == "price=null" for row in advice["cons"]))
        self.assertTrue(all({"text", "source", "evidence"} <= row.keys() for row in advice["pros"] + advice["cons"]))


class HttpSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.runtime = AgentRuntime(FakeAgent())
        self.server = create_server(self.runtime, "127.0.0.1", 0)
        self.server.RequestHandlerClass.log_message = lambda *args: None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def _post(self, path, payload):
        request = Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_static_and_json_http_contracts_include_versions_and_errors(self):
        with urlopen(self.base_url + "/", timeout=5) as response:
            page = response.read().decode("utf-8")
        self.assertIn('id="data-boundary"', page)
        self.assertIn('id="comparison"', page)
        self.assertIn('class="catalog-detail"', page)
        with urlopen(self.base_url + "/app.js", timeout=5) as response:
            app_js = response.read().decode("utf-8")
        self.assertIn("payload.error_code", app_js)
        self.assertIn('"session_expired"', app_js)
        self.assertIn("function renderComparison(handoff)", app_js)
        self.assertIn('api("/api/product"', app_js)

        with urlopen(self.base_url + "/api/health", timeout=5) as response:
            health = json.loads(response.read().decode("utf-8"))
        self.assertEqual(health["schema_version"], "show-me-your-agent.health.v1")
        self.assertEqual(health["catalog_products"], 1)

        status, session = self._post("/api/session", {})
        self.assertEqual(status, 201)
        self.assertEqual(session["schema_version"], "show-me-your-agent.session.v1")

        with self.assertRaises(HTTPError) as error:
            self._post(
                "/api/chat",
                {"session_id": session["session_id"], "message": "x" * 1001},
            )
        self.assertEqual(error.exception.code, 413)
        error_payload = json.loads(error.exception.read().decode("utf-8"))
        self.assertIn("1,000 characters or fewer", error_payload["error"])
        self.assertEqual(error_payload["error_code"], "message_too_long")

        status, chat = self._post(
            "/api/chat",
            {"session_id": session["session_id"], "message": "A blue shirt"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(chat["schema_version"], "show-me-your-agent.chat.v1")
        self.assertEqual(len(chat["products"]), 1)
        status, detail = self._post(
            "/api/product",
            {"session_id": session["session_id"], "parent_asin": "A1"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(detail["schema_version"], "show-me-your-agent.product-detail.v1")
        self.assertEqual(detail["parent_asin"], "A1")


if __name__ == "__main__":
    unittest.main()
