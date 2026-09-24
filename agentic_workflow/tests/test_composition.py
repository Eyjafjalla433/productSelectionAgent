import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from shopping_agent.retrieval import pure_cotton_matches
from mvp.server import AgentRuntime


class CompositionTests(unittest.TestCase):
    def test_explicit_purity_is_not_just_cotton(self):
        for message in ('必须纯棉T恤', '100% cotton tshirt', 'pure cotton tshirt', 'all-cotton tshirt'):
            parsed = TurnIntentRouter().understand_turn(message)
            self.assertTrue(any(u.slot == 'material' and '100% cotton' in u.values and u.constraint_type == 'hard'
                                for u in parsed.slot_updates), message)
        parsed = TurnIntentRouter().understand_turn('最好纯棉T恤')
        self.assertTrue(any(u.slot == 'material' and '100% cotton' in u.values and u.constraint_type == 'soft'
                            for u in parsed.slot_updates))

    def test_purity_requires_affirmative_noncontradictory_evidence(self):
        for title in ('100% cotton tee', 'Pure cotton tee', 'All-cotton tee', '100% organic cotton tee'):
            self.assertTrue(pure_cotton_matches({'title': title}), title)
        for title in ('Cotton tee', '95% cotton tee', 'Not 100% cotton tee', 'Cotton blend tee',
                      '不是纯棉T恤',
                      'Not made from 100% cotton', 'Pure cotton with acrylic',
                      '100% cotton tee; heather 50% cotton 50% polyester',
                      '100% cotton tee; 95% cotton variant'):
            self.assertFalse(pure_cotton_matches({'title': title}), title)
        self.assertFalse(pure_cotton_matches({'title': 'Tee', 'store': 'Pure Cotton'}))

    def test_search_filters_purity_and_undo_restores_it(self):
        products = {'pure': 'Black 100% cotton tshirt', 'blend': 'Black 60% cotton 40% polyester tshirt',
                    'unknown': 'Black cotton tshirt'}
        def search(query, top_k):
            return [{'product_id': key, 'score': 3-i} for i, key in enumerate(products)]
        def details(ids):
            return [dict(product_id=key, found=True, title=products[key], color='black') for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, '必须纯棉黑色T恤')
        self.assertEqual([p['parent_asin'] for p in first['products']], ['pure'])
        self.assertEqual(first['receipt']['hard']['material'], '100% cotton')
        broader = runtime.chat(sid, 'cotton is optional')
        self.assertNotIn('material', broader['receipt']['hard'])
        self.assertTrue(any(p['parent_asin'] == 'blend' for p in broader['products']))
        restored = runtime.chat(sid, 'undo')
        self.assertEqual([p['parent_asin'] for p in restored['products']], ['pure'])
        self.assertFalse(runtime.agent.errors)
