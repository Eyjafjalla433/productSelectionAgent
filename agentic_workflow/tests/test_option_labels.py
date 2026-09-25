import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from intent_router.option_labels import OPTION_LABELS_ZH, question_in_chinese
from mvp.server import AgentRuntime


class OptionLabelTests(unittest.TestCase):
    def test_every_displayed_label_roundtrips_through_its_question(self):
        for canonical, label in OPTION_LABELS_ZH.items():
            with self.subTest(option=canonical):
                question = {'target_slot': 'feature', 'constraint_type': 'soft',
                            'options': [canonical], 'option_labels': {canonical: label}}
                parsed = TurnIntentRouter().understand_turn(label, pending_question=question)
                self.assertEqual(len(parsed.slot_updates), 1)
                self.assertEqual(parsed.slot_updates[0].values, (canonical,))
                self.assertEqual(parsed.slot_updates[0].constraint_type, 'soft')

    def test_chinese_question_and_multi_detail_reply(self):
        text = question_in_chinese('color', ['black', 'blue', 'red'])
        self.assertEqual(text, '颜色更偏向黑色、蓝色还是红色？也可以先看看这批候选。')
        pending = {'target_slot': 'color', 'options': ['black'], 'option_labels': {'black': '黑色'}}
        parsed = TurnIntentRouter().understand_turn('黑色，必须纯棉', pending_question=pending)
        self.assertTrue(any(u.slot == 'material' and u.values == ('100% cotton',) for u in parsed.slot_updates))

    def test_displayed_option_and_extra_detail_are_both_collected(self):
        question = {'target_slot': 'material', 'constraint_type': 'soft',
                    'options': ['fleece'], 'option_labels': {'fleece': '抓绒'}}
        for message in ('抓绒，size M', 'size M, 抓绒', 'fleece and size M'):
            with self.subTest(message=message):
                parsed = TurnIntentRouter().understand_turn(message, pending_question=question)
                self.assertTrue(any(u.slot == 'material' and u.operation == 'set'
                                    and u.values == ('fleece',) and u.constraint_type == 'soft'
                                    for u in parsed.slot_updates))
                self.assertTrue(any(u.slot == 'size' and u.operation == 'set'
                                    and u.values == ('m',) for u in parsed.slot_updates))
        parsed = TurnIntentRouter().understand_turn('not fleece, size M', pending_question=question)
        self.assertFalse(any(u.slot == 'material' and u.operation == 'set'
                             and u.values == ('fleece',) for u in parsed.slot_updates))

    def test_runtime_option_reply_retains_added_size(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 50-i} for i in range(30)]
        def details(ids):
            return [dict(product_id=key, found=True, title=f"Black {'cotton' if int(key)%2 else 'fleece'} tshirt size M") for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I want a tshirt')
        self.assertEqual(first['receipt']['question']['target_slot'], 'material')
        reply = runtime.chat(sid, 'fleece, size M')
        state = runtime.agent.memory.snapshot(sid)
        self.assertEqual(state.soft_preferences['material'][0].value, 'fleece')
        self.assertEqual(state.hard_constraints['size'], 'm')
        self.assertTrue(reply['products'])

    def test_runtime_question_and_answer_keep_canonical_material(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 50-i} for i in range(30)]
        def details(ids):
            return [dict(product_id=key, found=True, title=f"Black {'cotton' if int(key)%2 else 'fleece'} tshirt") for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, '我想要T恤')
        self.assertIn('material', result['assistant']['message'])
        self.assertIn('fleece', result['assistant']['message'])
        self.assertNotRegex(result['assistant']['message'], r'[\u3400-\u9fff]')
        self.assertEqual(result['receipt']['question']['option_labels']['fleece'], 'fleece')
        runtime.chat(sid, '抓绒')
        self.assertEqual(runtime.agent.memory.snapshot(sid).soft_preferences['material'][0].value, 'fleece')
        self.assertFalse(runtime.agent.errors)
