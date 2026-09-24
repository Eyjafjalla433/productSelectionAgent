import unittest
from unittest.mock import patch
from types import ModuleType

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit


class SearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.results = [{'product_id': 'B', 'score': 2.5}, {'product_id': 'A', 'score': 1.3}]

    def search(self, query, top_k):
        self.calls.append((query, top_k))
        return self.results[:top_k]

    def details(self, ids):
        return [{'product_id': value, 'found': True, 'title': 'Blue cotton dress',
                 'brand': 'Example', 'color': 'blue', 'bullet_point': 'Cotton summer dress'} for value in ids]

    def runtime(self):
        return AgentRuntime(Agent(trace_enabled=True, search_function=self.search,
            details_function=self.details), orchestration_mode='adaptive')

    def test_complete_conversation_preserves_search_order_and_controls(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        response = runtime.chat(sid, 'I need a blue cotton dress')
        self.assertEqual([p['parent_asin'] for p in response['products']], ['B', 'A'])
        self.assertEqual([p['score'] for p in response['products']], [2.5, 1.3])
        self.assertIn('blue', self.calls[0][0])
        count = len(self.calls)
        runtime.product_detail(sid, 'B')
        runtime.chat(sid, 'Compare #1 and #2')
        final = runtime.chat(sid, 'Finalize my selection')
        self.assertEqual(final['selection_state']['status'], 'finalized')
        self.assertEqual(len(self.calls), count)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_rejection_is_applied_before_presentation(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue cotton dress')
        runtime.chat(sid, "I don't like #1")
        result = runtime.chat(sid, 'Show me more dresses')
        self.assertNotIn('B', [p['parent_asin'] for p in result['products']])

    def test_invalid_results_fail_closed(self):
        self.results = [{'product_id': 'B', 'score': float('nan')}]
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a dress')
        self.assertEqual(result['products'], [])
        self.assertEqual(runtime.agent.errors[-1]['stage'], '2_retrieval')

    def test_more_results_uses_unseen_products_before_repeating(self):
        agent = self.runtime().agent
        agent.reset('s', {})
        first = agent.respond('s', 'I need a blue cotton dress', 1, 1)
        second = agent.respond('s', 'Show me more dresses', 2, 1)
        self.assertEqual(first['recommendations'][0]['parent_asin'], 'B')
        self.assertEqual(second['recommendations'][0]['parent_asin'], 'A')

    def test_missing_price_does_not_satisfy_budget(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a blue dress under $50')
        self.assertEqual(result['products'], [])

    def test_default_entry_resolves_real_module_api(self):
        module = ModuleType('search_tool.tool')
        module.search_products = self.search
        module.get_product_details = self.details
        with patch.dict('sys.modules', {'search_tool.tool': module}):
            agent = Agent(trace_enabled=True)
            agent.reset('s', {})
            result = agent.respond('s', 'I need a blue cotton dress', 1, 1)
        self.assertEqual([p['parent_asin'] for p in result['recommendations']], ['B'])
        self.assertTrue(self.calls)


if __name__ == '__main__':
    unittest.main()
