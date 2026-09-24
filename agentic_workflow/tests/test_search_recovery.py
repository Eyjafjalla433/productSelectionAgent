import unittest
from unittest.mock import Mock

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from mvp.server import AgentRuntime
from mvp.audit import verify_audit


class SearchRecoveryTests(unittest.TestCase):
    def test_retry_never_becomes_pending_slot_answer(self):
        for message in ('retry', 'Please try again', '再试一次', '重试', '再搜一次'):
            for slot in ('color', 'brand', 'size', 'material', 'category'):
                parsed = TurnIntentRouter().understand_turn(message, pending_question={'target_slot': slot})
                self.assertEqual(parsed.slot_updates, ())
                self.assertTrue(parsed.decision_evidence['requested_results'])

    def test_failure_then_retry_preserves_requirements_without_model_or_extra_questions(self):
        calls = []
        def search(query, top_k):
            calls.append(query)
            if len(calls) == 1:
                raise TimeoutError('temporary search failure')
            return [{'product_id': 'black', 'score': 1}]
        def details(ids):
            return [dict(product_id=key, found=True, title='Black cotton tshirt', color='black') for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        failed = runtime.chat(sid, 'black cotton tshirt around $30')
        self.assertFalse(failed['products'])
        self.assertIn('saved', failed['assistant']['message'])
        self.assertNotIn('restate', failed['assistant']['message'])
        state = runtime.agent.memory.snapshot(sid)
        enhancer = Mock()
        runtime.agent.requirement_enhancer = enhancer
        result = runtime.chat(sid, '再试一次')
        enhancer.enhance.assert_not_called()
        self.assertTrue(result['products'])
        self.assertIsNone(result['assistant']['ask_attribute'])
        self.assertEqual(result['receipt']['hard'], failed['receipt']['hard'])
        self.assertEqual(runtime.agent.memory.snapshot(sid).hard_constraints, state.hard_constraints)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len(calls), 2)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_retry_without_product_context_does_not_search_randomly(self):
        search = Mock()
        runtime = AgentRuntime(Agent(search_function=search, details_function=Mock(), trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, '重试')
        search.assert_not_called()
        self.assertFalse(result['products'])
        self.assertNotIn('category', result['receipt']['hard'])
