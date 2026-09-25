import unittest
from pathlib import Path

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.control_intent import parse_control_intent
from mvp.server import AgentRuntime


class RankExplanationTests(unittest.TestCase):
    def runtime(self):
        catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
        return AgentRuntime(Agent(catalog_path=catalog, trace_enabled=True), orchestration_mode='adaptive')

    def test_answer_uses_displayed_evidence_without_new_search(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a blue cotton dress around $50')
        before = runtime.agent.memory.snapshot(sid)
        result = runtime.chat(sid, 'Why is #1 first?')
        explanation = result['receipt']['rank_explanation']
        self.assertEqual(result['receipt']['pre_reason'], 'rank_explanation')
        self.assertEqual(explanation['parent_asin'], first['products'][0]['parent_asin'])
        self.assertIn('blue', explanation['supported_requirements'])
        self.assertIn('cotton', explanation['supported_requirements'])
        self.assertIn('#1', result['assistant']['message'])
        self.assertIn('current relevance ranking', result['assistant']['message'])
        self.assertIn('not a verified quality advantage', result['assistant']['message'])
        source = runtime.agent.get_catalog_product(first['products'][0]['parent_asin'])
        source_text = ' '.join((source['title'], *source.get('features', ()), *source.get('description', ())))
        self.assertTrue(all(quote in source_text for quote in explanation['listing_highlights']))
        self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(runtime.agent.memory.snapshot(sid).hard_constraints, before.hard_constraints)
        self.assertIsNone(result['assistant']['ask_attribute'])
        self.assertFalse(any(event['stage'].startswith('2_retrieval')
                             for event in runtime.agent.get_trace(sid, result['turn'])['events']))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unpriced_budget_is_not_presented_as_a_verified_match(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]
        def details(ids):
            return [dict(product_id=i, found=True, title='Black cotton tshirt') for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt around $30')
        answer = runtime.chat(sid, 'Why #1?')
        self.assertTrue(answer['receipt']['rank_explanation']['budget_unverified'])
        self.assertIn('budget fit remains unverified', answer['assistant']['message'])
        self.assertEqual([p['parent_asin'] for p in answer['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_focus_and_mistaken_rank_are_handled_without_guessing(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a blue cotton dress')
        missing = runtime.chat(sid, 'Why did you pick it?')
        self.assertIn('Which product do you mean?', missing['assistant']['message'])
        second = runtime.chat(sid, 'Why did you pick #2?')
        self.assertEqual(second['receipt']['focused_product_id'], first['products'][1]['parent_asin'])
        focused = runtime.chat(sid, 'Why did you pick it?')
        self.assertEqual(focused['receipt']['rank_explanation']['rank'], 2)
        incorrect = runtime.chat(sid, 'Why is #2 first?')
        self.assertIn('#2 is currently ranked #2, not first.', incorrect['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_parser_does_not_swallow_other_product_questions(self):
        for message in ('Why #1?', 'Why is #1 first?', 'Why did you recommend #2?',
                        'What makes the second one a good match?'):
            self.assertEqual(parse_control_intent(message).action, 'explain_rank')
        self.assertIsNone(parse_control_intent('Why is #1 blue?'))


if __name__ == '__main__':
    unittest.main()
