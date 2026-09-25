import unittest

from mvp.audit import verify_audit
from mvp.demo import DEMO_CATALOG
from mvp.server import AgentRuntime


class CategoryAlternativesTests(unittest.TestCase):
    @staticmethod
    def runtime():
        return AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')

    def test_two_product_types_prompt_for_first_choice_and_keep_budget(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I am looking for a dress or a jacket under $50')
        self.assertEqual(first['receipt']['hard'], {'price_max': 50.0})
        self.assertEqual(first['receipt']['pre_reason'], 'choose_category_alternative')
        self.assertEqual(first['receipt']['question']['options'], ['dress', 'jacket'])
        self.assertIn('$50 limit is saved', first['assistant']['message'])

        both = runtime.chat(sid, 'Show me both')
        self.assertEqual(both['receipt']['pre_reason'], 'conversation_category_both')
        self.assertNotIn('category', both['receipt']['hard'])
        self.assertIn('one at a time', both['assistant']['message'])
        self.assertEqual(both['receipt']['suggested_replies'], ['Dress', 'Jacket'])

        chosen = runtime.chat(sid, 'Dress')
        self.assertEqual(chosen['receipt']['hard']['category'], 'dress')
        self.assertEqual(chosen['receipt']['hard']['price_max'], 50.0)
        self.assertTrue(chosen['products'])
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('category', undone['receipt']['hard'])
        self.assertEqual(undone['receipt']['hard']['price_max'], 50.0)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_either_is_fine_does_not_discard_category_choice(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a dress or a jacket')
        reply = runtime.chat(sid, 'Either is fine')
        self.assertEqual(reply['receipt']['pre_reason'], 'conversation_category_both')
        self.assertNotIn('category', reply['receipt']['hard'])
        self.assertEqual(reply['receipt']['suggested_replies'], ['Dress', 'Jacket'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_flexible_category_reply_keeps_the_choice_available(self):
        for reply in ("I'm open to either", 'No strong preference'):
            with self.subTest(reply=reply):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                runtime.chat(sid, 'I need a dress or a jacket')
                answer = runtime.chat(sid, reply)
                self.assertEqual(answer['receipt']['pre_reason'], 'conversation_category_both')
                self.assertNotIn('category', answer['receipt']['hard'])
                self.assertEqual(answer['receipt']['suggested_replies'], ['Dress', 'Jacket'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_alternative_question_keeps_the_current_page(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a blue dress')
        undecided = runtime.chat(sid, 'Maybe dresses or jackets')
        self.assertEqual(undecided['receipt']['pre_reason'], 'choose_category_alternative')
        self.assertEqual(undecided['receipt']['hard']['category'], 'dress')
        self.assertEqual(undecided['receipt']['display_mode'], 'retained_previous_results')
        self.assertEqual([p['parent_asin'] for p in undecided['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertNotIn('2_retrieval',
                         [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_between_and_two_item_request_do_not_silently_choose_first_type(self):
        for message in ('I am deciding between a dress and a jacket under $50',
                        'I need a dress and a jacket under $50'):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                result = runtime.chat(sid, message)
                self.assertEqual(result['receipt']['hard'], {'price_max': 50.0})
                self.assertEqual(result['receipt']['question']['options'], ['dress', 'jacket'])

    def test_choice_and_new_preference_are_kept_from_one_reply(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'A dress or a jacket under $50')
        chosen = runtime.chat(sid, 'Dress, preferably blue')
        self.assertEqual(chosen['receipt']['hard']['category'], 'dress')
        self.assertEqual(chosen['receipt']['hard']['price_max'], 50.0)
        self.assertEqual(chosen['receipt']['soft']['color'], ['blue'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_item_specific_colors_do_not_leak_across_product_types(self):
        for choice, category, color in (('T-shirt', 't-shirt', 'black'),
                                        ('Pants', 'pants', 'blue')):
            with self.subTest(choice=choice):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'I need a black T-shirt and blue jeans under $60')
                self.assertEqual(first['receipt']['hard'], {'price_max': 60.0})
                self.assertEqual(first['receipt']['question']['options'], ['t-shirt', 'pants'])
                self.assertIn('T-shirts or pants', first['assistant']['message'])
                self.assertEqual(first['receipt']['question']['scoped_details'][category]['color']['values'],
                                 (color,))
                if choice == 'T-shirt':
                    both = runtime.chat(sid, 'Show me both')
                    self.assertEqual(both['receipt']['suggested_replies'], ['T-shirt', 'Pants'])
                    self.assertNotIn('color', both['receipt']['hard'])
                chosen = runtime.chat(sid, choice)
                self.assertEqual(chosen['receipt']['hard']['category'], category)
                self.assertEqual(chosen['receipt']['hard']['color'], color)
                self.assertEqual(chosen['receipt']['hard']['price_max'], 60.0)
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_choice_detail_overrides_scoped_opening_detail(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a black T-shirt and blue jeans under $60')
        chosen = runtime.chat(sid, 'T-shirt, actually white')
        self.assertEqual(chosen['receipt']['hard']['category'], 't-shirt')
        self.assertEqual(chosen['receipt']['hard']['color'], 'white')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_item_details_survive_switch_undo_and_redo(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a black dress and blue jeans under $60')
        runtime.chat(sid, 'Dress')
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['category'], 'pants')
        self.assertEqual(jeans['receipt']['hard']['color'], 'blue')
        self.assertEqual(jeans['receipt']['hard']['price_max'], 60.0)
        previous = runtime.chat(sid, 'Undo')
        self.assertEqual(previous['receipt']['hard']['category'], 'dress')
        self.assertEqual(previous['receipt']['hard']['color'], 'black')
        redone = runtime.chat(sid, 'Redo')
        self.assertEqual(redone['receipt']['hard']['category'], 'pants')
        self.assertEqual(redone['receipt']['hard']['color'], 'blue')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_later_item_correction_is_remembered_without_leaking_to_other_item(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a black dress and blue jeans')
        runtime.chat(sid, 'Dress')
        runtime.chat(sid, 'Now the jeans')
        corrected = runtime.chat(sid, 'Actually green instead')
        self.assertEqual(corrected['receipt']['hard']['color'], 'green')
        dress = runtime.chat(sid, 'Back to the dress')
        self.assertEqual(dress['receipt']['hard']['color'], 'black')
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'green')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_new_unrelated_target_drops_deferred_details(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a black dress and blue jeans')
        runtime.chat(sid, 'Dress')
        runtime.chat(sid, 'Switch to shoes')
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertNotIn('color', jeans['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_start_over_clears_deferred_items_and_undo_restores_them(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a black dress and blue jeans')
        runtime.chat(sid, 'Dress')
        cleared = runtime.chat(sid, "Let's start over")
        self.assertEqual(cleared['receipt']['hard'], {})
        self.assertEqual(runtime.agent.memory.sessions[sid].deferred_category_details, {})
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['hard']['category'], 'dress')
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'blue')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_item_can_be_corrected_before_choosing_a_category_and_undone(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a black dress and blue jeans')
        edited = runtime.chat(sid, 'Actually make the jeans green')
        self.assertNotIn('category', edited['receipt']['hard'])
        self.assertEqual(edited['receipt']['pre_reason'], 'conversation_deferred_item_edit')
        self.assertIn('blue to green', edited['assistant']['message'])
        self.assertEqual(edited['receipt']['suggested_replies'], ['Dress', 'Pants'])
        self.assertEqual(runtime.agent.memory.sessions[sid].deferred_category_details['pants']['color']['values'],
                         ('green',))
        chosen = runtime.chat(sid, 'Jeans')
        self.assertEqual(chosen['receipt']['hard']['color'], 'green')
        self.assertEqual(chosen['receipt']['hard']['subtype'], 'jeans')
        runtime.chat(sid, 'Undo')
        self.assertEqual(runtime.agent.memory.sessions[sid].deferred_category_details['pants']['color']['values'],
                         ('green',))
        runtime.chat(sid, 'Undo')
        self.assertEqual(runtime.agent.memory.sessions[sid].deferred_category_details['pants']['color']['values'],
                         ('blue',))
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'blue')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_item_edit_keeps_current_results_and_requirements(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        dress = runtime.chat(sid, 'Dress')
        self.assertTrue(dress['products'])
        edited = runtime.chat(sid, 'Make the jeans green')
        self.assertEqual(edited['receipt']['hard'], dress['receipt']['hard'])
        self.assertEqual([p['parent_asin'] for p in edited['products']],
                         [p['parent_asin'] for p in dress['products']])
        self.assertIn('current dress search stays put', edited['assistant']['message'])
        self.assertNotIn('2_retrieval',
                         [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'green')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_item_edit_can_add_shared_budget_before_category_choice(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a black dress and blue jeans')
        edited = runtime.chat(sid, 'Actually make the jeans green and keep both under $50')
        self.assertEqual(edited['receipt']['hard'], {'price_max': 50.0})
        self.assertEqual(edited['receipt']['suggested_replies'], ['Dress', 'Pants'])
        self.assertIn('$50 limit is saved for both', edited['assistant']['message'])
        jeans = runtime.chat(sid, 'Jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'green')
        self.assertEqual(jeans['receipt']['hard']['price_max'], 50.0)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_item_edit_with_shared_budget_rechecks_active_results(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        runtime.chat(sid, 'Dress')
        edited = runtime.chat(sid, 'Make the jeans green and keep both under $50')
        self.assertEqual(edited['receipt']['hard']['category'], 'dress')
        self.assertEqual(edited['receipt']['hard']['color'], 'blue')
        self.assertEqual(edited['receipt']['hard']['price_max'], 50.0)
        self.assertIn('green for the jeans', edited['assistant']['message'])
        self.assertIn('2_retrieval',
                      [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])
        self.assertTrue(all(product['match']['hard_supported'] == product['match']['hard_total']
                            for product in edited['products']))
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'green')
        self.assertEqual(jeans['receipt']['hard']['price_max'], 50.0)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_item_only_budget_does_not_leak_to_other_category(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        dress = runtime.chat(sid, 'Dress')
        edited = runtime.chat(sid, 'Make the jeans green and under $50')
        self.assertNotIn('price_max', edited['receipt']['hard'])
        self.assertEqual(edited['products'], dress['products'])
        self.assertIn('price limit to $50', edited['assistant']['message'])
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['price_max'], 50.0)
        self.assertEqual(jeans['receipt']['hard']['color'], 'green')
        back = runtime.chat(sid, 'Now the dress')
        self.assertNotIn('price_max', back['receipt']['hard'])
        undone = runtime.chat(sid, 'Undo')
        self.assertEqual(undone['receipt']['hard']['price_max'], 50.0)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_item_only_budget_restores_shared_baseline_on_switch(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans under $80')
        dress = runtime.chat(sid, 'Dress')
        self.assertEqual(dress['receipt']['hard']['price_max'], 80.0)
        edited = runtime.chat(sid, 'Make the jeans green and under $50')
        self.assertEqual(edited['receipt']['hard']['price_max'], 80.0)
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['price_max'], 50.0)
        back = runtime.chat(sid, 'Now the dress')
        self.assertEqual(back['receipt']['hard']['price_max'], 80.0)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_item_only_budget_edit_can_be_undone_before_selection(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans under $80')
        runtime.chat(sid, 'Make the jeans green and under $50')
        self.assertEqual(runtime.agent.memory.sessions[sid].deferred_category_details['pants']['price_max']['values'],
                         (50.0,))
        runtime.chat(sid, 'Undo')
        self.assertNotIn('price_max',
                         runtime.agent.memory.sessions[sid].deferred_category_details['pants'])
        jeans = runtime.chat(sid, 'Jeans')
        self.assertEqual(jeans['receipt']['hard']['price_max'], 80.0)
        self.assertEqual(jeans['receipt']['hard']['color'], 'black')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_first_choice_color_stays_soft_and_ordered_across_switches(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        dress = runtime.chat(sid, 'Dress')
        edited = runtime.chat(sid, 'I would prefer green jeans, but blue is fine')
        self.assertEqual(edited['receipt']['hard'], dress['receipt']['hard'])
        self.assertEqual([item['parent_asin'] for item in edited['products']],
                         [item['parent_asin'] for item in dress['products']])
        self.assertIn('green first, with blue also okay', edited['assistant']['message'])
        jeans = runtime.chat(sid, 'Show me the jeans')
        self.assertNotIn('color', jeans['receipt']['hard'])
        self.assertEqual(jeans['receipt']['ordered_preferences']['color'],
                         {'preferred': 'green', 'also_acceptable': 'blue'})
        runtime.chat(sid, 'Show me the dress')
        jeans_again = runtime.chat(sid, 'Show me the jeans')
        self.assertEqual(jeans_again['receipt']['ordered_preferences']['color'],
                         {'preferred': 'green', 'also_acceptable': 'blue'})
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_soft_preference_edit_can_be_undone(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        runtime.chat(sid, 'I would prefer green jeans, but blue is fine')
        restored = runtime.chat(sid, 'Undo')
        self.assertNotIn('category', restored['receipt']['hard'])
        jeans = runtime.chat(sid, 'Jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'black')
        self.assertNotIn('color', jeans['receipt']['ordered_preferences'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_color_can_be_left_open_without_switching_search(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        dress = runtime.chat(sid, 'Dress')
        edited = runtime.chat(sid, 'Actually, any color is fine for the jeans')
        self.assertEqual(edited['receipt']['hard'], dress['receipt']['hard'])
        self.assertEqual([item['parent_asin'] for item in edited['products']],
                         [item['parent_asin'] for item in dress['products']])
        self.assertIn('no color restriction', edited['assistant']['message'])
        self.assertNotIn('color', runtime.agent.memory.sessions[sid].deferred_category_details['pants'])
        jeans = runtime.chat(sid, 'Show me the jeans')
        self.assertNotIn('color', jeans['receipt']['hard'])
        self.assertNotIn('color', jeans['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_color_clear_is_reversible_before_selection(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        edited = runtime.chat(sid, 'No color preference for the jeans')
        self.assertEqual(edited['receipt']['suggested_replies'], ['Dress', 'Pants'])
        runtime.chat(sid, 'Undo')
        jeans = runtime.chat(sid, 'Jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'black')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_clear_and_new_size_share_one_undoable_turn(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        edited = runtime.chat(sid, 'No color preference for the jeans, but size L')
        self.assertNotIn('category', edited['receipt']['hard'])
        scoped = runtime.agent.memory.sessions[sid].deferred_category_details['pants']
        self.assertNotIn('color', scoped)
        self.assertEqual(scoped['size']['values'], ('l',))
        runtime.chat(sid, 'Undo')
        restored = runtime.agent.memory.sessions[sid].deferred_category_details['pants']
        self.assertEqual(restored['color']['values'], ('black',))
        self.assertNotIn('size', restored)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_color_exclusion_stays_with_item_and_is_undoable(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        dress = runtime.chat(sid, 'Dress')
        edited = runtime.chat(sid, 'Any color except red for the jeans')
        self.assertEqual(edited['receipt']['hard'], dress['receipt']['hard'])
        self.assertEqual([item['parent_asin'] for item in edited['products']],
                         [item['parent_asin'] for item in dress['products']])
        self.assertIn('any color except red', edited['assistant']['message'])
        scoped = runtime.agent.memory.sessions[sid].deferred_category_details['pants']
        self.assertNotIn('color', scoped)
        self.assertEqual(scoped['color_exclude']['values'], ('red',))
        jeans = runtime.chat(sid, 'Show me the jeans')
        self.assertNotIn('color', jeans['receipt']['hard'])
        self.assertEqual(jeans['receipt']['excluded']['color'], ['red'])
        back = runtime.chat(sid, 'Show me the dress')
        self.assertNotIn('color', back['receipt']['excluded'])
        undone = runtime.chat(sid, 'Undo')
        self.assertEqual(undone['receipt']['excluded']['color'], ['red'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_exclusion_edit_undo_restores_previous_color(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress and black jeans')
        runtime.chat(sid, 'Any color except red for the jeans')
        runtime.chat(sid, 'Undo')
        jeans = runtime.chat(sid, 'Jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'black')
        self.assertNotIn('color', jeans['receipt']['excluded'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_except_color_is_exclusion_in_single_item_search(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need jeans, any color except red')
        self.assertEqual(result['receipt']['hard']['category'], 'pants')
        self.assertNotIn('color', result['receipt']['hard'])
        self.assertEqual(result['receipt']['excluded']['color'], ['red'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_correction_is_not_mistaken_for_alternatives(self):
        router = self.runtime().agent.router
        parsed = router.understand_turn('I need a dress, actually a jacket')
        self.assertNotIn('category_alternatives', parsed.decision_evidence)
        self.assertEqual([u.values for u in parsed.slot_updates if u.slot == 'category'],
                         [('jacket',)])

    def test_rejected_categories_are_not_offered_as_choices(self):
        router = self.runtime().agent.router
        parsed = router.understand_turn("I don't want a dress or a jacket")
        self.assertNotIn('category_alternatives', parsed.decision_evidence)


if __name__ == '__main__':
    unittest.main()
