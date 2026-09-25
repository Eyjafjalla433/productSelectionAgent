import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.demo import DEMO_CATALOG
from mvp.server import AgentRuntime


class UnverifiableBudgetTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def search(query, top_k):
            self.calls.append(query)
            return [{'product_id': 'TEE', 'score': 1.0}]

        def details(ids):
            return [{'product_id': 'TEE', 'found': True,
                     'title': 'Black cotton T-shirt', 'color': 'black'}]

        self.agent = Agent(search_function=search, details_function=details,
                           trace_enabled=True)
        self.agent.reset('budget', {})

    def test_unpriced_source_offers_explicit_choice_and_undo(self):
        first = self.agent.respond('budget', 'I need a black T-shirt under $50', 1)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(first['ask_attribute'], 'budget')
        self.assertFalse(first['recommendations'])
        self.assertEqual(state.hard_constraints['price_max'], 50)
        self.assertEqual(state.pending_question['reason'], 'unverifiable_budget')
        self.assertIn('catalog has no prices', first['message'])
        second = self.agent.respond('budget', 'Show unpriced ideas', 2)
        state = self.agent.memory.snapshot('budget')
        self.assertNotIn('price_max', state.hard_constraints)
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 50)
        self.assertEqual(len(second['recommendations']), 1)
        self.assertIn("doesn't include prices", second['message'])
        self.agent.respond('budget', 'Undo', 3)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(state.hard_constraints['price_max'], 50)
        self.assertNotIn('budget_target', state.soft_preferences)

    def test_optional_catalog_question_still_discloses_missing_prices(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 30 - i} for i in range(30)]

        def details(ids):
            return [dict(product_id=item, found=True,
                         title=('Black cotton' if int(item) % 2 else 'White linen') + ' T-shirt')
                    for item in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a T-shirt around $30')
        self.assertEqual(result['receipt']['soft']['budget_target'], ['30.0'])
        self.assertTrue(result['products'])
        self.assertIsNotNone(result['receipt']['question'])
        self.assertIn('no prices', result['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_preview_reply_can_add_other_details_in_same_turn(self):
        self.agent.respond('budget', 'I need a T-shirt under $50', 1)
        second = self.agent.respond('budget', 'Show unpriced ideas, black please', 2)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(state.hard_constraints['color'], 'black')
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 50)
        self.assertTrue(second['recommendations'])
        self.agent.respond('budget', 'Undo', 3)
        state = self.agent.memory.snapshot('budget')
        self.assertNotIn('color', state.hard_constraints)
        self.assertEqual(state.hard_constraints['price_max'], 50)

    def test_keep_strict_does_not_repeat_question_or_search(self):
        self.agent.respond('budget', 'I need a black T-shirt under $50', 1)
        count = len(self.calls)
        second = self.agent.respond('budget', 'Keep strict limit', 2)
        self.assertEqual(len(self.calls), count)
        self.assertFalse(second['recommendations'])
        self.assertIn('keep your price limit strict', second['message'])
        self.assertEqual(self.agent.memory.snapshot('budget').hard_constraints['price_max'], 50)
        third = self.agent.respond('budget', 'Show unpriced ideas', 3)
        self.assertTrue(third['recommendations'])

    def test_price_explanation_requires_a_match_on_other_constraints(self):
        def details(ids):
            return [{'product_id': 'TEE', 'found': True, 'title': 'Blue running shoes'}]
        self.agent.retriever.details_function = details
        first = self.agent.respond('budget', 'I need a black T-shirt under $50', 1)
        self.assertNotIn('catalog has no prices', first['message'])
        self.assertNotEqual(self.agent.memory.snapshot('budget').pending_question.get('reason'),
                            'unverifiable_budget')

    def test_natural_preview_and_strict_phrases(self):
        for phrase, preview in [('Show me anyway', True),
                                ("I'd like to see unpriced options", True),
                                ('Keep it strict', False)]:
            with self.subTest(phrase=phrase):
                self.agent.reset('budget', {})
                self.agent.respond('budget', 'I need a T-shirt under $50', 1)
                result = self.agent.respond('budget', phrase, 2)
                self.assertEqual(bool(result['recommendations']), preview)

    def test_web_runtime_exposes_budget_choice_and_audits_preview(self):
        runtime = AgentRuntime(self.agent, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a black T-shirt under $50')
        self.assertFalse(first['products'])
        self.assertEqual(first['receipt']['question']['options'],
                         ['show unpriced ideas', 'keep strict limit'])
        self.assertIn('catalog has no prices', first['assistant']['message'])
        second = runtime.chat(sid, 'Show unpriced ideas')
        self.assertTrue(second['products'])
        self.assertNotIn('price_max', second['receipt']['hard'])
        self.assertEqual(second['receipt']['soft']['budget_target'], ['50.0'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_existing_approximate_target_survives_cap_preview(self):
        self.agent.respond('budget', 'I need a T-shirt around $30 and under $50', 1)
        self.agent.respond('budget', 'Show unpriced ideas', 2)
        state = self.agent.memory.snapshot('budget')
        self.assertNotIn('price_max', state.hard_constraints)
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)

    def test_price_floor_preview_preserves_its_meaning_and_undo(self):
        result = self.agent.respond('budget', 'I need a T-shirt over $50', 1)
        self.assertIn('your $50 minimum', result['message'])
        self.assertEqual(self.agent.memory.snapshot('budget').hard_constraints['price_min'], 50)
        preview = self.agent.respond('budget', 'Show unpriced ideas', 2)
        self.assertTrue(preview['recommendations'])
        state = self.agent.memory.snapshot('budget')
        self.assertNotIn('price_min', state.hard_constraints)
        self.assertEqual(state.soft_preferences['budget_floor_target'][0].value, 50)
        self.assertNotIn('budget_target', state.soft_preferences)
        self.assertIn('prices', preview['message'])
        self.agent.respond('budget', 'Undo', 3)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(state.hard_constraints['price_min'], 50)
        self.assertNotIn('budget_floor_target', state.soft_preferences)

    def test_noncanonical_range_keeps_both_bounds_through_preview_and_undo(self):
        first = self.agent.respond('budget', 'I need a T-shirt over $30 but under $50', 1)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(state.hard_constraints['price_min'], 30)
        self.assertEqual(state.hard_constraints['price_max'], 50)
        self.assertIn('$30–$50 range', first['message'])
        preview = self.agent.respond('budget', 'Show unpriced ideas', 2)
        state = self.agent.memory.snapshot('budget')
        self.assertTrue(preview['recommendations'])
        self.assertNotIn('price_min', state.hard_constraints)
        self.assertNotIn('price_max', state.hard_constraints)
        self.assertEqual(state.soft_preferences['budget_floor_target'][0].value, 30)
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 50)
        self.assertNotIn('30', self.calls[-1])  # The index cannot validate a price floor.
        self.agent.respond('budget', 'Undo', 3)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(state.hard_constraints['price_min'], 30)
        self.assertEqual(state.hard_constraints['price_max'], 50)
        self.assertNotIn('budget_floor_target', state.soft_preferences)

    def test_negated_more_than_does_not_create_a_false_floor(self):
        parsed = self.agent.router.understand_turn('I need a T-shirt no more than $50')
        set_slots = {update.slot: update.values for update in parsed.slot_updates
                     if update.operation == 'set'}
        self.assertEqual(set_slots['price_max'], (50.0,))
        self.assertNotIn('price_min', set_slots)

    def test_clearing_budget_also_clears_unverified_floor(self):
        self.agent.respond('budget', 'I need a T-shirt over $30 but under $50', 1)
        self.agent.respond('budget', 'Show unpriced ideas', 2)
        self.agent.respond('budget', 'Remove my budget', 3)
        state = self.agent.memory.snapshot('budget')
        self.assertNotIn('budget_floor_target', state.soft_preferences)
        self.assertNotIn('budget_target', state.soft_preferences)

    def test_preview_floor_survives_category_change_but_named_withdrawal_removes_it(self):
        self.agent.respond('budget', 'I need a T-shirt over $30', 1)
        self.agent.respond('budget', 'Show unpriced ideas', 2)
        self.agent.respond('budget', 'Switch to shoes', 3)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(state.hard_constraints['category'], 'shoes')
        self.assertEqual(state.soft_preferences['budget_floor_target'][0].value, 30)
        self.agent.respond('budget', 'Remove the price floor', 4)
        self.assertNotIn('budget_floor_target',
                         self.agent.memory.snapshot('budget').soft_preferences)

    def test_new_hard_cap_removes_only_incompatible_preview_target(self):
        self.agent.respond('budget', 'I need a T-shirt over $30 but under $50', 1)
        self.agent.respond('budget', 'Show unpriced ideas', 2)
        changed = self.agent.respond('budget', 'Actually under $40', 3)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(state.hard_constraints['price_max'], 40)
        self.assertEqual(state.soft_preferences['budget_floor_target'][0].value, 30)
        self.assertNotIn('budget_target', state.soft_preferences)
        self.assertEqual(state.pending_question['reason'], 'unverifiable_budget')
        self.agent.respond('budget', 'Show unpriced ideas', 4)
        state = self.agent.memory.snapshot('budget')
        self.assertEqual(state.soft_preferences['budget_floor_target'][0].value, 30)
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 40)
        self.agent.respond('budget', 'Undo', 5)
        self.assertEqual(self.agent.memory.snapshot('budget').hard_constraints['price_max'], 40)

    def test_priced_catalog_can_satisfy_hard_cap_without_preview(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a blue dress under $50')
        self.assertTrue(result['products'])
        self.assertTrue(all(product['price'] <= 50 for product in result['products']))
        self.assertNotEqual((result['receipt']['question'] or {}).get('reason'),
                            'unverifiable_budget')

    def test_priced_catalog_can_verify_noncanonical_range(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a blue dress over $30 but under $50')
        self.assertTrue(result['products'])
        self.assertTrue(all(30 <= product['price'] <= 50
                            for product in result['products']))
        self.assertEqual(result['receipt']['hard']['price_min'], 30.0)
        self.assertEqual(result['receipt']['hard']['price_max'], 50.0)


if __name__ == '__main__':
    unittest.main()
