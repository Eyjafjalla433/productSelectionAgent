import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from mvp.audit import verify_audit
from mvp.server import AgentRuntime, _preference_summary


class PreferenceWithdrawalTests(unittest.TestCase):
    def test_preference_recap_leads_with_shopping_details_without_empty_inventory(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        before = runtime.chat(sid, 'black tshirt, preferably cotton')
        recap = runtime.chat(sid, 'What are my preferences?')
        text = recap['assistant']['message']
        self.assertIn('in this session', text)
        self.assertIn('Prefer: cotton.', text)
        self.assertNotIn('cotton material', text)
        self.assertNotIn('account-wide', text)
        self.assertNotIn('No products are saved', text)
        self.assertEqual(recap['products'], before['products'])
        self.assertEqual(recap['receipt']['hard'], before['receipt']['hard'])
        self.assertEqual(recap['receipt']['soft'], before['receipt']['soft'])
        self.assertEqual(recap['receipt']['session_choice_summary'], {'saved_asins': [], 'hidden_asins': []})
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_recap_uses_plain_labels_without_changing_preference_data(self):
        receipt = {'hard': {'category': 't-shirt'},
                   'soft': {'material': ['cotton'], 'style': ['regular fit'],
                            'use_case': ['travel'], 'brand': ['Example']}, 'excluded': {}}
        text, state = _preference_summary(receipt)
        self.assertIn('Prefer: cotton, regular fit, for travel, by Example.', text)
        self.assertEqual(state, receipt)

    def test_natural_memory_question_preserves_an_open_choice(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 24 - i} for i in range(24)]

        def details(ids):
            return [dict(product_id=key, found=True,
                         title=('Black' if int(key) < 12 else 'White') + ' T-shirt')
                    for key in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a T-shirt')
        pending = runtime.agent.memory.pending[sid].copy()
        count = len(calls)
        self.assertEqual(pending['target_slot'], 'color')
        for wording in ('What do you know about me?',
                        'What information do you have about me?'):
            with self.subTest(wording=wording):
                summary = runtime.chat(sid, wording)
                self.assertEqual(len(calls), count)
                self.assertEqual(summary['receipt']['pre_reason'], 'preference_summary')
                self.assertIn('not an account-wide memory report',
                              summary['assistant']['message'])
                self.assertEqual(summary['receipt']['question']['options'], pending['options'])
                self.assertEqual(runtime.agent.memory.pending[sid], pending)
                self.assertEqual([item['parent_asin'] for item in summary['products']],
                                 [item['parent_asin'] for item in first['products']])
        chosen = runtime.chat(sid, 'black')
        self.assertEqual(chosen['receipt']['soft']['color'], ['black'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    @staticmethod
    def runtime():
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True, price=20,
                         title=('Black cotton tshirt' if int(i) < 6 else 'Blue cotton tshirt'))
                    for i in ids]

        return AgentRuntime(Agent(search_function=search, details_function=details,
                                  trace_enabled=True), orchestration_mode='adaptive')

    def test_memory_recap_discloses_session_choices_without_changing_them(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        saved_asin = first['products'][0]['parent_asin']
        hidden_asin = first['products'][1]['parent_asin']
        runtime.chat(sid, 'Select #1')
        runtime.chat(sid, 'Reject #2')
        recap = runtime.chat(sid, 'What do you remember about me?')
        self.assertIn('in this session', recap['assistant']['message'])
        self.assertIn('not an account-wide memory report', recap['assistant']['message'])
        self.assertIn('Saved for comparison:', recap['assistant']['message'])
        self.assertIn('Hidden from future results:', recap['assistant']['message'])
        self.assertEqual(recap['receipt']['session_choice_summary'], {
            'saved_asins': [saved_asin], 'hidden_asins': [hidden_asin],
        })
        self.assertEqual(recap['selection_state']['selected_asins'], [saved_asin])
        self.assertEqual(recap['receipt']['pre_reason'], 'preference_summary')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_remove_one_allowed_color_preserves_the_other_and_undo(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black or blue tshirt')
        self.assertEqual(first['receipt']['hard']['color'], ('black', 'blue'))
        changed = runtime.chat(sid, 'Remove the black requirement')
        self.assertEqual(changed['receipt']['hard']['color'], 'blue')
        self.assertNotIn('color', changed['receipt']['excluded'])
        self.assertTrue(changed['products'])
        self.assertTrue(all('blue' in product['title'].lower() for product in changed['products']))
        recap = runtime.chat(sid, 'What are my preferences?')
        self.assertIn('blue color', recap['assistant']['message'])
        self.assertNotIn('black color', recap['assistant']['message'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard']['color'], ('black', 'blue'))
        self.assertEqual([product['parent_asin'] for product in restored['products']],
                         [product['parent_asin'] for product in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_remove_color_while_reaffirming_material_is_one_edit(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt')
        changed = runtime.chat(sid, 'Remove the black requirement, but keep cotton')
        self.assertNotIn('color', changed['receipt']['hard'])
        self.assertEqual(changed['receipt']['hard']['material'], 'cotton')
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_forget_named_preference_does_not_turn_it_into_a_requirement(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt')
        forgotten = runtime.chat(sid, 'Forget the cotton preference')
        self.assertNotIn('material', forgotten['receipt']['hard'])
        self.assertNotIn('material', forgotten['receipt']['soft'])
        self.assertNotIn('material', forgotten['receipt']['excluded'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_remove_budget_keeps_other_requirements_and_is_undoable(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt around $30')
        self.assertEqual(first['receipt']['soft']['budget_target'], ['30.0'])
        changed = runtime.chat(sid, 'Remove my $30 budget')
        self.assertNotIn('budget_target', changed['receipt']['soft'])
        self.assertEqual(changed['receipt']['hard']['color'], 'black')
        self.assertNotIn('price_max', changed['receipt']['hard'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['soft']['budget_target'], ['30.0'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_remove_only_price_cap_keeps_target_and_can_add_new_limit(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt under $30')
        changed = runtime.chat(sid, 'Remove the price cap, but around $40')
        self.assertNotIn('price_max', changed['receipt']['hard'])
        self.assertEqual(changed['receipt']['hard']['color'], 'black')
        self.assertEqual(changed['receipt']['soft']['budget_target'], ['40.0'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard']['price_max'], 30.0)
        self.assertNotIn('budget_target', restored['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_plain_language_indifference_clears_only_named_dimension(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt, I prefer slim fit')
        self.assertEqual(first['receipt']['soft']['style'], ['slim fit'])
        changed = runtime.chat(sid, "I don't care about fit anymore")
        self.assertNotIn('style', changed['receipt']['soft'])
        self.assertEqual(changed['receipt']['hard']['color'], 'black')
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['soft']['style'], ['slim fit'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_indifference_can_reaffirm_another_requirement_in_one_turn(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt')
        changed = runtime.chat(sid, "I don't care about color anymore, but keep cotton")
        self.assertNotIn('color', changed['receipt']['hard'])
        self.assertEqual(changed['receipt']['hard']['material'], 'cotton')
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_indifference_is_dimension_scoped_not_product_specific(self):
        router = TurnIntentRouter()
        updates = router.understand_turn("I don't care about price anymore").slot_updates
        self.assertEqual({update.slot for update in updates if update.operation == 'clear'},
                         {'price_min', 'price_max', 'budget_target', 'budget_floor_target'})
        product_comment = router.understand_turn("I don't care about the fit of #2").slot_updates
        self.assertFalse(any(update.operation == 'clear' and update.slot == 'style'
                             for update in product_comment))
        black_item = router.understand_turn("I don't care about the black one").slot_updates
        self.assertFalse(any(update.operation == 'remove_value' and update.slot == 'color'
                             for update in black_item))

    def test_plain_language_value_withdrawal_preserves_other_allowed_color(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black or blue tshirt')
        changed = runtime.chat(sid, "I don't care about black anymore")
        self.assertEqual(changed['receipt']['hard']['color'], 'blue')
        self.assertNotIn('color', changed['receipt']['excluded'])
        self.assertTrue(all('blue' in item['title'].lower() for item in changed['products']))
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_value_withdrawal_can_add_price_limit_without_rejecting_value(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt')
        changed = runtime.chat(sid, "I don't care about cotton anymore, but under $30")
        self.assertNotIn('material', changed['receipt']['hard'])
        self.assertNotIn('material', changed['receipt']['excluded'])
        self.assertEqual(changed['receipt']['hard']['color'], 'black')
        self.assertEqual(changed['receipt']['hard']['price_max'], 30.0)
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
