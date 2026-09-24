import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from mvp.server import AgentRuntime
from mvp.audit import verify_audit


class PreferenceScopeTests(unittest.TestCase):
    def test_hard_and_soft_cues_apply_to_their_own_clause(self):
        cases = [
            ('I prefer black but must have pure cotton', {'color': 'soft', 'material': 'hard'}),
            ('最好黑色但必须纯棉', {'color': 'soft', 'material': 'hard'}),
            ('I need cotton but preferably blue', {'material': 'hard', 'color': 'soft'}),
            ('必须棉质不过颜色最好蓝色', {'material': 'hard', 'color': 'soft'}),
            ('I prefer cotton but size M is required', {'material': 'soft', 'size': 'hard'}),
            ('I prefer black and must have cotton', {'color': 'soft', 'material': 'hard'}),
            ('I prefer black and cotton', {'color': 'soft', 'material': 'soft'}),
            ('cotton is not required but black is essential', {'material': 'soft', 'color': 'hard'}),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                updates = TurnIntentRouter().understand_turn(text).slot_updates
                actual = {u.slot: u.constraint_type for u in updates if u.operation == 'set'}
                for slot, tier in expected.items():
                    self.assertEqual(actual[slot], tier)

    def test_mixed_priorities_filter_products_and_survive_history(self):
        catalog = {'blend': 'Black 50% cotton 50% polyester tshirt',
                   'pure': 'White 100% cotton tshirt'}
        def search(query, top_k):
            return [{'product_id': key, 'score': 2-i} for i, key in enumerate(catalog)]
        def details(ids):
            return [dict(product_id=key, found=True, title=catalog[key]) for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a tshirt, preferably black but must have pure cotton')
        self.assertEqual(result['receipt']['hard']['material'], '100% cotton')
        self.assertNotIn('color', result['receipt']['hard'])
        self.assertEqual([p['parent_asin'] for p in result['products']], ['pure'])
        self.assertIn('preference for black', result['assistant']['message'])
        self.assertIn('alternatives', result['assistant']['message'])
        runtime.chat(sid, 'cotton is optional')
        result = runtime.chat(sid, 'undo')
        self.assertEqual(result['receipt']['hard']['material'], '100% cotton')
        self.assertEqual([p['parent_asin'] for p in result['products']], ['pure'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
