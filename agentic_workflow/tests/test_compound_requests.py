import unittest

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit
from mvp.control_intent import split_detail_and_requirements


class CompoundRequestTests(unittest.TestCase):
    def test_polite_replacement_request_changes_requirement_and_can_be_undone(self):
        for message in ('Can it be blue instead?', 'Could it be blue instead?',
                        'Can we switch to blue instead?'):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'black tshirt')
                result = runtime.chat(sid, message)
                self.assertEqual(result['receipt']['hard']['color'], 'blue')
                self.assertTrue(result['products'])
                self.assertTrue(all('Blue' in p['title'] for p in result['products']))
                restored = runtime.chat(sid, 'undo')
                self.assertEqual(restored['receipt']['hard']['color'], 'black')
                self.assertEqual([p['parent_asin'] for p in restored['products']], [p['parent_asin'] for p in first['products']])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_product_possibility_question_does_not_change_color(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        result = runtime.chat(sid, 'Could #2 be available in blue?')
        self.assertEqual(result['receipt']['hard']['color'], 'black')
        self.assertEqual(result['receipt']['pre_reason'], 'product_detail')
        self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])

    def runtime(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]
        def details(ids):
            return [dict(product_id=i, found=True, title=f"{'Black' if int(i) < 6 else 'Blue'} cotton tshirt",
                         bullet_point='100% cotton') for i in ids]
        return AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')

    def test_answer_old_item_then_update_and_undo_one_turn(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        message = '第二款是什么材质？另外换成蓝色'
        result = runtime.chat(sid, message)
        self.assertEqual(result['turn'], 2)
        self.assertIn('previous list', result['assistant']['message'])
        self.assertIn('100% cotton', result['assistant']['message'])
        self.assertEqual(result['receipt']['hard']['color'], 'blue')
        self.assertTrue(result['products'])
        self.assertTrue(all('Blue' in p['title'] for p in result['products']))
        self.assertEqual(result['receipt']['compound_request']['detail_context']['parent_asin'], first['products'][1]['parent_asin'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard']['color'], 'black')
        self.assertEqual([p['parent_asin'] for p in restored['products']], [p['parent_asin'] for p in first['products']])
        audit = runtime.audit(sid)
        self.assertEqual(audit['turns'][1]['user_message'], message)
        self.assertEqual(verify_audit(audit), [])

    def test_unresolved_reference_does_not_guess_new_product(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        result = runtime.chat(sid, '它是什么材质？另外换成蓝色')
        self.assertIn('could not identify', result['assistant']['message'])
        self.assertEqual(result['receipt']['hard']['color'], 'blue')
        self.assertIsNone(result['receipt']['compound_request']['detail_context'])

    def test_explicit_connector_preserves_multislot_tail(self):
        control, tail = split_detail_and_requirements('What material is the second one made of? Also change to blue, under $30')
        self.assertEqual(control.ranks, (2,))
        self.assertEqual(tail, 'change to blue, under $30')
        self.assertIsNone(split_detail_and_requirements('第二款是什么材质'))
        self.assertIsNone(split_detail_and_requirements('第二款多少钱？另外确认选择'))
