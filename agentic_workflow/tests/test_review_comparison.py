import unittest
from pathlib import Path

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.control_intent import parse_control_intent
from mvp.server import AgentRuntime


class ReviewComparisonTests(unittest.TestCase):
    def priced_runtime(self):
        catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
        return AgentRuntime(Agent(catalog_path=catalog, trace_enabled=True), orchestration_mode='adaptive')

    def unreviewed_runtime(self):
        calls = []
        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]
        def details(ids):
            return [dict(product_id=i, found=True, title='Black cotton tshirt') for i in ids]
        return AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                            orchestration_mode='adaptive'), calls

    def test_unreviewed_source_keeps_list_and_states_limit(self):
        runtime, calls = self.unreviewed_runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        before = len(calls)
        reply = runtime.chat(sid, 'Which one has the best reviews?')
        self.assertEqual(reply['receipt']['pre_reason'], 'review_comparison')
        self.assertEqual(reply['receipt']['review_comparison']['rated_count'], 0)
        self.assertIn("can't identify a best-reviewed one", reply['assistant']['message'])
        self.assertEqual([p['parent_asin'] for p in reply['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(len(calls), before)
        self.assertIsNone(reply['assistant']['ask_attribute'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_average_and_review_volume_are_reported_separately(self):
        runtime = self.priced_runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        by_average = max(first['products'], key=lambda p: p['rating'])
        by_count = max(first['products'], key=lambda p: p['rating_count'])
        reply = runtime.chat(sid, 'Which one has the best reviews?')
        self.assertIn(f"#{by_average['rank']}", reply['assistant']['message'])
        self.assertIn(f"{by_average['rating']:g}/5", reply['assistant']['message'])
        self.assertIn(f"#{by_count['rank']} has the most ratings", reply['assistant']['message'])
        self.assertIn("haven't checked review text or live scores", reply['assistant']['message'])
        self.assertEqual(reply['receipt']['review_comparison']['winner_ranks'], [by_average['rank']])
        self.assertEqual([p['parent_asin'] for p in reply['products']], [p['parent_asin'] for p in first['products']])
        self.assertFalse(any(event['stage'].startswith('2_retrieval')
                             for event in runtime.agent.get_trace(sid, reply['turn'])['events']))
        most = runtime.chat(sid, 'Which one has the most reviews?')
        self.assertEqual(most['receipt']['review_comparison']['mode'], 'most_reviews')
        self.assertEqual(most['receipt']['review_comparison']['winner_ranks'], [by_count['rank']])
        self.assertIn(f"{by_count['rating_count']:,}", most['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_missing_average_is_not_filled_from_other_items(self):
        runtime = self.priced_runtime()
        runtime.agent.retriever.products['DEMO-DRESS-002']['average_rating'] = None
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        reply = runtime.chat(sid, 'Which is highest rated?')
        self.assertEqual(reply['receipt']['review_comparison']['unknown_rating_count'], 1)
        self.assertIn("can't rule them out", reply['assistant']['message'])
        self.assertEqual([p['parent_asin'] for p in reply['products']], [p['parent_asin'] for p in first['products']])

    def test_zero_count_and_invalid_rating_cannot_win(self):
        runtime = self.priced_runtime()
        product = runtime.agent.retriever.products['DEMO-DRESS-002']
        product['average_rating'] = 5.0
        product['rating_number'] = 0
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a dress')
        suspect = next(p for p in first['products'] if p['parent_asin'] == 'DEMO-DRESS-002')
        reply = runtime.chat(sid, 'Which is highest rated?')
        self.assertNotIn(suspect['rank'], reply['receipt']['review_comparison']['winner_ranks'])
        self.assertEqual(reply['receipt']['review_comparison']['unknown_rating_count'], 1)

    def test_comparison_parser_does_not_swallow_product_requirements(self):
        for message in ('Which one has the best reviews?', 'Which is highest rated?',
                        'Which one has the most ratings?'):
            self.assertEqual(parse_control_intent(message).action, 'review_compare')
        self.assertIsNone(parse_control_intent('I want a highly rated dress'))
        self.assertEqual(parse_control_intent('How much does #2 cost?').action, 'detail')


if __name__ == '__main__':
    unittest.main()
