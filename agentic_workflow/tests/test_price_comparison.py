import unittest
from pathlib import Path

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.control_intent import parse_control_intent
from mvp.server import AgentRuntime


class PriceComparisonTests(unittest.TestCase):
    def runtime(self, *, priced):
        if priced:
            catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
            return (AgentRuntime(Agent(catalog_path=catalog, trace_enabled=True),
                                 orchestration_mode='adaptive'), None)
        calls = []
        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]
        def details(ids):
            prices = (30, 25, 20, 35, 40, 15, 29, 31, 19, 27, 33, 22)
            return [dict(product_id=i, found=True, title='Black cotton tshirt',
                         price=prices[int(i)] if priced else None) for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        return runtime, calls

    def test_unpriced_request_is_honest_and_does_not_replace_results(self):
        runtime, calls = self.runtime(priced=False)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        before = len(calls)
        result = runtime.chat(sid, 'Something cheaper, please')
        self.assertEqual(len(calls), before)
        self.assertEqual(result['receipt']['pre_reason'], 'price_comparison')
        self.assertEqual(result['receipt']['price_comparison']['priced_count'], 0)
        self.assertIn("can't tell which is cheaper", result['assistant']['message'])
        self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(result['receipt']['hard'], first['receipt']['hard'])
        self.assertIsNone(result['assistant']['ask_attribute'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_priced_request_requires_baseline_then_compares_known_prices(self):
        runtime, calls = self.runtime(priced=True)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        before = len(runtime.agent.trace)
        question = runtime.chat(sid, 'Do you have anything cheaper?')
        self.assertIn('Cheaper than which item?', question['assistant']['message'])
        result = runtime.chat(sid, 'Cheaper than #2')
        baseline_price = float(first['products'][1]['price'])
        expected = [p['rank'] for p in first['products'] if float(p['price']) < baseline_price]
        self.assertEqual(result['receipt']['price_comparison']['baseline_rank'], 2)
        self.assertEqual(result['receipt']['price_comparison']['cheaper_ranks'],
                         sorted(expected, key=lambda rank: (float(first['products'][rank-1]['price']), rank)))
        self.assertEqual(result['receipt']['focused_product_id'], first['products'][1]['parent_asin'])
        self.assertIn('catalog prices, not live offers', result['assistant']['message'])
        self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(len(runtime.agent.trace), before + 2)
        self.assertFalse(any(event['stage'].startswith('2_retrieval')
                             for event in runtime.agent.get_trace(sid, result['turn'])['events']))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_cheapest_uses_only_displayed_known_prices(self):
        runtime, calls = self.runtime(priced=True)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        before = len(runtime.agent.trace)
        result = runtime.chat(sid, 'Which one is cheapest?')
        lowest = min(float(p['price']) for p in first['products'])
        self.assertIn(f'${lowest:g}', result['assistant']['message'])
        self.assertIn('not live offers', result['assistant']['message'])
        self.assertEqual(result['receipt']['price_comparison']['priced_count'], len(first['products']))
        self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(len(runtime.agent.trace), before + 1)
        self.assertFalse(any(event['stage'].startswith('2_retrieval')
                             for event in runtime.agent.get_trace(sid, result['turn'])['events']))

    def test_missing_baseline_price_is_not_inferred_from_other_items(self):
        runtime, _ = self.runtime(priced=True)
        runtime.agent.retriever.products['DEMO-DRESS-002']['price'] = None
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        unknown = next(p for p in first['products'] if p['parent_asin'] == 'DEMO-DRESS-002')
        reply = runtime.chat(sid, f"Cheaper than #{unknown['rank']}")
        self.assertIn(f"don't have a catalog price for #{unknown['rank']}", reply['assistant']['message'])
        self.assertEqual(reply['receipt']['price_comparison']['baseline_rank'], unknown['rank'])
        self.assertEqual(reply['receipt']['price_comparison']['cheaper_ranks'], [])
        self.assertEqual(reply['receipt']['focused_product_id'], unknown['parent_asin'])
        self.assertEqual([p['parent_asin'] for p in reply['products']], [p['parent_asin'] for p in first['products']])
        cheapest = runtime.chat(sid, 'Which one is cheapest?')
        self.assertEqual(cheapest['receipt']['price_comparison']['unknown_count'], 1)
        self.assertIn("can't rule them out", cheapest['assistant']['message'])

    def test_ranked_unpriced_request_still_tracks_the_referenced_item(self):
        runtime, calls = self.runtime(priced=False)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        before = len(calls)
        reply = runtime.chat(sid, 'Cheaper than #2')
        self.assertEqual(reply['receipt']['focused_product_id'], first['products'][1]['parent_asin'])
        self.assertEqual(reply['receipt']['price_comparison']['baseline_rank'], 2)
        self.assertEqual(len(calls), before)

    def test_price_comparison_language_does_not_swallow_budget_or_detail(self):
        for message in ('Something cheaper', 'Cheaper than #2', 'Which one is cheapest?'):
            self.assertEqual(parse_control_intent(message).action, 'price_compare')
        self.assertIsNone(parse_control_intent('I want a cheaper blue dress'))
        self.assertIsNone(parse_control_intent('Under $30, please'))
        self.assertEqual(parse_control_intent('How much does #2 cost?').action, 'detail')


if __name__ == '__main__':
    unittest.main()
