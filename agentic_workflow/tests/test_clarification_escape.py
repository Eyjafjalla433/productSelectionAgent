import unittest

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit
from shopping_agent.policy import conversational_choice_question


class ClarificationEscapeTests(unittest.TestCase):
    def test_browsing_pace_survives_a_small_preference_edit(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 40 - i} for i in range(40)]

        def details(ids):
            return [dict(product_id=key, found=True,
                         title=f"{'Black' if int(key) % 2 else 'White'} "
                               f"{'Cotton' if int(key) < 20 else 'Polyester'} T-shirt")
                    for key in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I am just browsing T-shirts')
        self.assertIsNone(first['receipt']['question'])
        refined = runtime.chat(sid, 'Cotton would be nice')
        self.assertEqual(refined['receipt']['soft']['material'], ['cotton'])
        self.assertIsNone(refined['receipt']['question'])
        self.assertTrue(refined['products'])
        guided = runtime.chat(sid, 'Help me choose')
        self.assertEqual(guided['receipt']['question']['target_slot'], 'color')
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('material', undone['receipt']['soft'])
        self.assertEqual(undone['receipt']['hard']['category'], 't-shirt')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_browsing_shows_results_before_optional_question(self):
        for message in ('I am just browsing T-shirts', 'Show me T-shirts for ideas'):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                result = runtime.chat(sid, message)
                self.assertTrue(result['products'])
                self.assertIsNone(result['receipt']['question'])
                self.assertEqual(result['receipt']['post_reason'], 'browse_first')
                self.assertIn('No need to narrow things down yet', result['assistant']['message'])
                self.assertEqual(runtime.agent.memory.sessions[sid].intent.value, 'browsing')
                focused = runtime.chat(sid, 'Fabric matters more to me')
                self.assertEqual(focused['receipt']['question']['target_slot'], 'material')
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        ready = runtime.chat(sid, 'I need a T-shirt')
        self.assertEqual(ready['receipt']['question']['target_slot'], 'color')

    def test_shopper_can_move_from_browsing_to_guided_choice(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        browsing = runtime.chat(sid, 'I am just browsing T-shirts')
        self.assertIsNone(browsing['receipt']['question'])
        guided = runtime.chat(sid, "Now I'm ready to buy; help me choose")
        self.assertEqual(guided['receipt']['question']['target_slot'], 'color')
        self.assertEqual(guided['receipt']['post_reason'], 'candidate_information_gain')
        self.assertIn('Happy to help you narrow it down', guided['assistant']['message'])
        self.assertTrue(guided['products'])
        self.assertEqual(guided['receipt']['hard'], browsing['receipt']['hard'])
        self.assertEqual([item['parent_asin'] for item in guided['products']],
                         [item['parent_asin'] for item in browsing['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_shopper_can_return_to_browsing_without_changing_page(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I am just browsing T-shirts')
        guided = runtime.chat(sid, 'Help me choose')
        self.assertEqual(guided['receipt']['question']['target_slot'], 'color')
        browsing = runtime.chat(sid, 'Actually, I just want to browse')
        self.assertEqual(browsing['receipt']['pre_reason'], 'conversation_browse_again')
        self.assertIsNone(browsing['receipt']['question'])
        self.assertIsNone(runtime.agent.memory.pending[sid])
        self.assertEqual(runtime.agent.memory.sessions[sid].intent.value, 'browsing')
        self.assertEqual(browsing['receipt']['hard'], guided['receipt']['hard'])
        self.assertEqual([item['parent_asin'] for item in browsing['products']],
                         [item['parent_asin'] for item in guided['products']])
        self.assertIn('no pressure', browsing['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_shopper_named_focus_retargets_candidate_question(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        self.assertEqual(first['receipt']['question']['target_slot'], 'color')
        focused = runtime.chat(sid, 'Fabric matters more to me')
        self.assertEqual(focused['receipt']['question']['target_slot'], 'material')
        self.assertNotIn('material', focused['receipt']['hard'])
        self.assertNotIn('material', focused['receipt']['soft'])
        self.assertTrue(focused['products'])
        self.assertIn('cotton or linen', focused['assistant']['message'])
        answer = runtime.chat(sid, 'linen')
        self.assertEqual(answer['receipt']['soft']['material'], ['linen'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_most_important_fabric_is_a_focus_not_a_material(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        focused = runtime.chat(sid, 'Fabric matters most to me')
        self.assertEqual(focused['receipt']['question']['target_slot'], 'material')
        self.assertNotIn('material', focused['receipt']['hard'])
        self.assertNotIn('material', focused['receipt']['soft'])
        self.assertTrue(focused['products'])

    def test_focus_can_share_turn_with_answer_to_previous_question(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        focused = runtime.chat(sid, 'Fabric matters more to me, and black')
        self.assertEqual(focused['receipt']['soft']['color'], ['black'])
        self.assertEqual(focused['receipt']['question']['target_slot'], 'material')
        self.assertNotIn('material', focused['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_show_results_now_overrides_optional_focus_question(self):
        for message in ('Fabric matters more to me, but show me the results now',
                        'Fabric matters most to me, but show me first'):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'I need a tshirt')
                result = runtime.chat(sid, message)
                self.assertIsNone(result['receipt']['question'])
                self.assertIsNone(result['assistant']['ask_attribute'])
                self.assertTrue(result['products'])
                self.assertNotIn('material', result['receipt']['hard'])
                self.assertNotIn('material', result['receipt']['soft'])
                self.assertEqual([item['parent_asin'] for item in result['products']],
                                 [item['parent_asin'] for item in first['products']])
                self.assertEqual(result['receipt']['conversation_act'], 'show_current_results')
                self.assertNotIn('2_retrieval',
                                 [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']])
                self.assertIsNone(runtime.agent.memory.pending[sid])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_show_me_first_retains_list_but_show_me_more_searches(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        retained = runtime.chat(sid, 'Show me first')
        self.assertEqual([item['parent_asin'] for item in retained['products']],
                         [item['parent_asin'] for item in first['products']])
        self.assertEqual(retained['receipt']['display_mode'], 'retained_previous_results')
        self.assertNotIn('2_retrieval',
                         [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']])
        more = runtime.chat(sid, 'Show me more')
        self.assertTrue(more['products'])
        self.assertFalse({item['parent_asin'] for item in more['products']} &
                         {item['parent_asin'] for item in first['products']})
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_show_me_more_explains_when_no_unseen_products_remain(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 3 - i} for i in range(3)]

        def details(ids):
            return [dict(product_id=item, found=True, title='Cotton T-shirt') for item in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        more = runtime.chat(sid, 'Show me more')
        self.assertEqual([item['parent_asin'] for item in more['products']],
                         [item['parent_asin'] for item in first['products']])
        self.assertIn("couldn't find any new", more['assistant']['message'].lower())
        self.assertEqual(more['receipt']['new_product_count'], 0)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_exhausted_more_keeps_the_last_visible_batch(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 12 - i} for i in range(12)]

        def details(ids):
            return [dict(product_id=item, found=True, title='Cotton T-shirt') for item in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        second = runtime.chat(sid, 'Show me more')
        self.assertEqual(len(first['products']), 10)
        self.assertEqual(len(second['products']), 2)
        exhausted = runtime.chat(sid, 'Show me more')
        self.assertEqual([item['parent_asin'] for item in exhausted['products']],
                         [item['parent_asin'] for item in second['products']])
        self.assertEqual(exhausted['receipt']['display_mode'], 'retained_previous_results')
        self.assertEqual(exhausted['receipt']['new_product_count'], 0)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_undo_after_exhausted_more_restores_the_page_shopper_saw(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 12 - i} for i in range(12)]

        def details(ids):
            return [dict(product_id=item, found=True,
                         title=('Black' if int(item) < 6 else 'White') + ' cotton T-shirt')
                    for item in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        last_page = runtime.chat(sid, 'Show me more')
        runtime.chat(sid, 'Show me more')
        changed = runtime.chat(sid, 'Black only')
        self.assertEqual(changed['receipt']['hard']['color'], 'black')
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual([item['parent_asin'] for item in restored['products']],
                         [item['parent_asin'] for item in last_page['products']])
        self.assertEqual(restored['receipt']['restored_from_turn'], last_page['turn'])
        redone = runtime.chat(sid, 'Redo')
        self.assertEqual(redone['receipt']['hard']['color'], 'black')
        restored_again = runtime.chat(sid, 'Undo')
        self.assertEqual([item['parent_asin'] for item in restored_again['products']],
                         [item['parent_asin'] for item in last_page['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_optional_answer_and_new_budget_are_kept_in_one_turn(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        self.assertEqual(first['receipt']['question']['target_slot'], 'color')
        answered = runtime.chat(sid, 'Black, and around $30 please')
        self.assertEqual(answered['receipt']['soft']['color'], ['black'])
        self.assertEqual(answered['receipt']['soft']['budget_target'], ['30.0'])
        self.assertTrue(answered['products'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_focus_survives_missing_category_until_a_product_type_is_named(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'Fabric matters more to me')
        self.assertEqual(first['receipt']['question']['target_slot'], 'category')
        self.assertNotIn('material', first['receipt']['hard'])
        second = runtime.chat(sid, 'T-shirt')
        self.assertEqual(second['receipt']['question']['target_slot'], 'material')
        self.assertIsNone(runtime.agent.memory.context[sid]['pending_focus'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_shopper_can_change_question_focus_before_category(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'Fabric matters more to me')
        changed = runtime.chat(sid, 'Actually style matters more')
        self.assertEqual(changed['receipt']['question']['target_slot'], 'category')
        self.assertNotIn('category', changed['receipt']['hard'])
        result = runtime.chat(sid, 'T-shirt')
        self.assertEqual(result['receipt']['question']['target_slot'], 'style')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_focus_without_reliable_catalog_split_keeps_results(self):
        runtime = self.runtime(uniform=True)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        focused = runtime.chat(sid, 'I care most about fabric')
        self.assertEqual(focused['receipt']['post_reason'], 'focus_not_distinguished')
        self.assertIsNone(focused['receipt']['question'])
        self.assertTrue(focused['products'])
        self.assertNotIn('material', focused['receipt']['hard'])
        self.assertIn("won't guess", focused['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_repeated_focus_without_catalog_split_keeps_results_without_search(self):
        runtime = self.runtime(uniform=True)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        focused = runtime.chat(sid, 'Fabric matters most to me')
        self.assertEqual(focused['receipt']['post_reason'], 'focus_not_distinguished')
        repeated = runtime.chat(sid, 'Fabric matters most to me')
        self.assertEqual(repeated['receipt']['pre_reason'], 'conversation_focus_repeat')
        self.assertIn("don't give me a reliable split", repeated['assistant']['message'])
        self.assertEqual([item['parent_asin'] for item in repeated['products']],
                         [item['parent_asin'] for item in focused['products']])
        self.assertNotIn('2_retrieval',
                         [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_repeated_focus_does_not_repeat_the_same_optional_question(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        focused = runtime.chat(sid, 'Fabric matters more to me')
        self.assertEqual(focused['receipt']['question']['target_slot'], 'material')
        pending = runtime.agent.memory.pending[sid].copy()
        repeated = runtime.chat(sid, 'Fabric matters most to me')
        self.assertIsNone(repeated['receipt']['question'])
        self.assertIsNone(repeated['assistant']['ask_attribute'])
        self.assertTrue(repeated['products'])
        self.assertIn('fabric', repeated['assistant']['message'].lower())
        self.assertEqual([item['parent_asin'] for item in repeated['products']],
                         [item['parent_asin'] for item in focused['products']])
        self.assertEqual(runtime.agent.memory.pending[sid], pending)
        self.assertNotIn('2_retrieval',
                         [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])
        answered = runtime.chat(sid, 'linen')
        self.assertEqual(answered['receipt']['soft']['material'], ['linen'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unrelated_detail_bypasses_optional_question_without_erasing_it(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        self.assertEqual(first['receipt']['question']['target_slot'], 'color')
        updated = runtime.chat(sid, 'Maybe size M')
        self.assertEqual(updated['receipt']['soft']['size'], ['m'])
        self.assertNotIn('color', updated['receipt']['hard'])
        self.assertIsNone(updated['assistant']['ask_attribute'])
        self.assertIsNone(runtime.agent.memory.pending[sid])
        self.assertEqual(updated['receipt']['post_reason'], 'shopper_supplied_other_detail')
        self.assertIn('leave color open', updated['assistant']['message'])
        self.assertIn('size M', updated['assistant']['message'])
        self.assertEqual(len(updated['products']), 10)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unrelated_hard_detail_still_allows_empty_result_recovery(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        updated = runtime.chat(sid, 'Size M is required')
        self.assertEqual(updated['receipt']['hard']['size'], 'm')
        self.assertEqual(updated['products'], [])
        self.assertEqual(updated['receipt']['post_reason'], 'empty_eligible_pool')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_undo_restores_ten_product_order_after_empty_search(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        empty = runtime.chat(sid, 'blue instead')
        self.assertEqual(empty['products'], [])
        result = runtime.chat(sid, 'undo')
        self.assertEqual(len(result['products']), 10)
        self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(result['receipt']['restored_from_turn'], 1)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_uncertainty_retains_ids_and_order_without_retrieval(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        pending = runtime.agent.memory.pending[sid].copy()
        result = runtime.chat(sid, '我还没想好')
        self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(runtime.agent.memory.pending[sid], pending)
        self.assertEqual(result['receipt']['display_mode'], 'retained_previous_results')
        self.assertIn('take your time', result['assistant']['message'])
        self.assertNotIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, 2)['events']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_uncertainty_with_explicit_more_still_searches(self):
        for reply in ("I'm not sure, show me more", '还没想好，换一批'):
            runtime = self.runtime()
            sid = runtime.new_session()['session_id']
            first = runtime.chat(sid, 'I need a tshirt')
            result = runtime.chat(sid, reply)
            self.assertIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, 2)['events']])
            self.assertNotEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
            self.assertIsNone(result['assistant']['ask_attribute'])

    def test_uncertainty_after_empty_search_does_not_resurrect_stale_products(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        runtime.chat(sid, 'under $30')
        result = runtime.chat(sid, '不知道')
        self.assertEqual(result['products'], [])
        self.assertEqual(result['receipt']['hard']['price_max'], 30)
        self.assertIn('no verified matches', result['assistant']['message'])
        self.assertNotIn('这几款', result['assistant']['message'])

    def test_uncertainty_never_becomes_a_slot_value(self):
        from intent_router.turn_router import TurnIntentRouter
        for target in ('category', 'color', 'material', 'brand', 'size', 'style'):
            for reply in ("I'm not sure", '不知道', '我还没想好', '不确定，先看看'):
                with self.subTest(target=target, reply=reply):
                    parsed = TurnIntentRouter().understand_turn(reply, pending_question={'target_slot': target})
                    self.assertEqual(parsed.slot_updates, ())
                    self.assertTrue(parsed.decision_evidence['requested_results'])

    def test_uncertainty_with_details_keeps_new_requirements_and_stops_questions(self):
        for reply in ('不确定，但要黑色棉质', "I'm not sure, but black cotton please"):
            runtime = self.runtime()
            sid = runtime.new_session()['session_id']
            runtime.chat(sid, 'I need a tshirt')
            result = runtime.chat(sid, reply)
            self.assertEqual(result['receipt']['hard']['color'], 'black')
            self.assertEqual(result['receipt']['hard']['material'], 'cotton')
            self.assertIsNone(result['assistant']['ask_attribute'])
            self.assertTrue(result['products'])
            self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_uncertainty_does_not_relax_existing_hard_constraints(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt under $30')
        result = runtime.chat(sid, "I don't know")
        self.assertEqual(result['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(result['products'], [])
        self.assertIsNone(result['assistant']['ask_attribute'])

    def test_pure_uncertainty_does_not_call_optional_model(self):
        from unittest.mock import Mock
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        enhancer = Mock()
        runtime.agent.requirement_enhancer = enhancer
        result = runtime.chat(sid, '我还没想好')
        enhancer.enhance.assert_not_called()
        self.assertTrue(result['products'])
        self.assertIsNone(result['assistant']['ask_attribute'])

    def test_new_product_target_can_ask_previously_asked_dimension(self):
        def search(query, top_k):
            category = 'shoes' if 'shoes' in query else 't-shirt'
            return [{'product_id': f'{category}:{i}', 'score': 50-i} for i in range(40)]
        def details(ids):
            return [dict(product_id=key, found=True,
                         title=f"{'black' if int(key.split(':')[1]) % 2 else 'white'} cotton {key.split(':')[0]}",
                         color='black' if int(key.split(':')[1]) % 2 else 'white') for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt around $30')
        self.assertEqual(first['receipt']['question']['target_slot'], 'color')
        # The old target's exhausted question budget must not suppress a new one.
        runtime.agent.memory.context[sid]['clarification_streak'] = 2
        second = runtime.chat(sid, 'switch to shoes')
        self.assertEqual(second['receipt']['question']['target_slot'], 'color')
        state = runtime.agent.memory.snapshot(sid)
        self.assertEqual(state.suggestions['clarification_streak'], 1)
        self.assertEqual(len(state.asked_questions), 2)
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)
        self.assertTrue(second['products'])
        self.assertTrue(all('shoes' in p['title'] for p in second['products']))
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard']['category'], 't-shirt')
        self.assertIsNone(restored['assistant']['ask_attribute'])
        self.assertEqual(restored['receipt']['display_mode'], 'restored_previous_results')
        self.assertEqual([p['parent_asin'] for p in restored['products']], [p['parent_asin'] for p in first['products']])
        self.assertTrue(restored['products'])
        self.assertEqual(runtime.agent.memory.snapshot(sid).suggestions['question_scope_start'], 3)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_named_indifference_does_not_erase_answer_to_another_question(self):
        from intent_router.turn_router import TurnIntentRouter
        router = TurnIntentRouter()
        for reply in ('材质无所谓，但要黑色', 'no preference for material, black please',
                      "material doesn't matter, black please"):
            with self.subTest(reply=reply):
                parsed = router.understand_turn(reply, pending_question={'target_slot': 'color', 'constraint_type': 'soft'})
                updates = parsed.slot_updates
                self.assertTrue(any(u.slot == 'material' and u.operation == 'clear' for u in updates))
                self.assertFalse(any(u.slot == 'color' and u.operation == 'clear' for u in updates))
                self.assertTrue(any(u.slot == 'color' and 'black' in u.values for u in updates))

    def test_generic_indifference_can_include_another_requirement(self):
        from intent_router.turn_router import TurnIntentRouter
        for reply in ('都行，但要棉质', 'any is fine, but cotton please'):
            parsed = TurnIntentRouter().understand_turn(reply, pending_question={'target_slot': 'color', 'constraint_type': 'soft'})
            self.assertTrue(any(u.slot == 'color' and u.operation == 'clear' for u in parsed.slot_updates))
            self.assertTrue(any(u.slot == 'material' and 'cotton' in u.values for u in parsed.slot_updates))

    def test_multi_detail_reply_reaches_filtered_products(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        runtime.agent.memory.pending[sid] = {'target_slot': 'color', 'constraint_type': 'soft'}
        result = runtime.chat(sid, '材质无所谓，但要黑色')
        self.assertEqual(result['receipt']['hard']['color'], 'black')
        self.assertNotIn('material', result['receipt']['hard'])
        self.assertTrue(result['products'])
        self.assertTrue(all('black' in p['title'].lower() for p in result['products']))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def runtime(self, uniform=False):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 50-i} for i in range(min(48, top_k))]
        def details(ids):
            rows = []
            for key in ids:
                i = int(key)
                color = 'black' if uniform or i % 2 else 'white'
                material = 'cotton' if uniform or (i//2) % 2 else 'linen'
                style = 'casual' if uniform or (i//4) % 2 else 'formal'
                rows.append(dict(product_id=key, found=True, title=f'{color} {material} {style} t-shirt', color=color))
            return rows
        return AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')

    def test_broad_pool_asks_grounded_question_and_shows_products(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a tshirt')
        self.assertEqual(result['receipt']['post_reason'], 'candidate_information_gain')
        question = result['receipt']['question']
        self.assertGreaterEqual(question['evidence']['expected_reduction'], .3)
        self.assertTrue(question['options'])
        self.assertTrue(result['products'])
        self.assertIn('show me first', result['assistant']['message'])
        self.assertIn('all in the mix', result['assistant']['message'])
        self.assertNotIn('These options differ in', result['assistant']['message'])

    def test_candidate_question_copy_uses_human_attribute_names(self):
        for attribute, wording in (('color', 'listed colors'),
                                   ('material', 'fabric preference'),
                                   ('style', 'styles'),
                                   ('use_case', 'how you plan to use it')):
            with self.subTest(attribute=attribute):
                message = conversational_choice_question(attribute,
                                                         ['black', 'blue', 'white'])
                self.assertIn(wording, message)
                self.assertIn('black, blue, or white', message)
                self.assertIn('show me first', message)
                self.assertNotIn('use_case', message)

    def test_at_most_two_consecutive_questions_even_with_useful_answers(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a tshirt')
        for _ in range(2):
            question = result['receipt']['question']
            self.assertIsNotNone(question)
            result = runtime.chat(sid, 'I prefer ' + question['options'][0])
        self.assertIsNone(result['assistant']['ask_attribute'])
        self.assertEqual(result['receipt']['post_reason'], 'clarification_streak_limit')
        self.assertTrue(result['products'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_changing_question_focus_cannot_restart_optional_interrogation(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        opening = runtime.chat(sid, 'I need a tshirt')
        self.assertEqual(opening['receipt']['question']['target_slot'], 'color')
        fabric = runtime.chat(sid, 'Fabric matters more to me')
        self.assertEqual(fabric['receipt']['question']['target_slot'], 'material')

        fit = runtime.chat(sid, 'Fit matters more to me')
        self.assertIsNone(fit['receipt']['question'])
        self.assertEqual(fit['receipt']['pre_reason'], 'conversation_focus_attention_limit')
        self.assertIn('asked enough narrowing questions', fit['assistant']['message'])
        self.assertTrue(fit['products'])
        self.assertEqual([p['parent_asin'] for p in fit['products']],
                         [p['parent_asin'] for p in fabric['products']])
        self.assertNotIn('2_retrieval',
                         [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])

        color = runtime.chat(sid, 'Color matters more to me')
        self.assertIsNone(color['receipt']['question'])
        self.assertEqual(color['receipt']['pre_reason'], 'conversation_focus_already_covered')
        self.assertIn("won't ask the same question again", color['assistant']['message'])
        self.assertEqual([p['parent_asin'] for p in color['products']],
                         [p['parent_asin'] for p in fit['products']])
        self.assertNotIn('2_retrieval',
                         [event['stage'] for event in runtime.agent.get_trace(sid, 4)['events']])
        chosen = runtime.chat(sid, 'I prefer black')
        self.assertEqual(chosen['receipt']['soft']['color'], ['black'])
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('color', undone['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_skip_and_no_preference_always_exit_to_results(self):
        for reply in ('先看看', 'show me first', '都行', 'any is fine', 'thanks'):
            with self.subTest(reply=reply):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                runtime.chat(sid, 'I need a tshirt')
                result = runtime.chat(sid, reply)
                self.assertIsNone(result['assistant']['ask_attribute'])
                self.assertTrue(result['products'])
                self.assertNotIn(reply, str(result['receipt']['hard']))

    def test_declining_scored_options_does_not_save_neither_as_a_preference(self):
        for reply in ('neither', 'no preference'):
            with self.subTest(reply=reply):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'I need a tshirt')
                self.assertTrue(first['receipt']['question']['options'])
                result = runtime.chat(sid, reply)
                self.assertEqual(result['receipt']['conversation_act'], 'declined_options')
                self.assertEqual(result['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(result['receipt']['soft'], first['receipt']['soft'])
                self.assertEqual([item['parent_asin'] for item in result['products']],
                                 [item['parent_asin'] for item in first['products']])
                self.assertIsNone(result['assistant']['ask_attribute'])
                self.assertIsNone(runtime.agent.memory.pending[sid])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_declining_options_can_keep_an_extra_requirement(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        self.assertTrue(first['receipt']['question']['options'])
        changed = runtime.chat(sid, 'neither, but cotton please')
        self.assertTrue(changed['receipt']['declined_options'])
        self.assertNotIn('neither', str(changed['receipt']['hard']))
        self.assertNotIn('neither', str(changed['receipt']['soft']))
        self.assertEqual(changed['receipt']['hard']['material'], 'cotton')
        self.assertIsNone(changed['assistant']['ask_attribute'])
        self.assertIn("won't favor either", changed['assistant']['message'])
        restored = runtime.chat(sid, 'undo')
        self.assertNotIn('material', restored['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_accepting_all_offered_options_ends_optional_question(self):
        for reply in ('Either is fine', 'Any of those is fine', 'All are okay'):
            with self.subTest(reply=reply):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'I need a tshirt')
                self.assertTrue(first['receipt']['question']['options'])
                result = runtime.chat(sid, reply)
                self.assertEqual(result['receipt']['conversation_act'], 'declined_options')
                self.assertEqual(result['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(result['receipt']['soft'], first['receipt']['soft'])
                self.assertIn('choices open', result['assistant']['message'])
                self.assertNotIn('couldn\'t connect', result['assistant']['message'])
                self.assertIsNone(runtime.agent.memory.pending[sid])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_flexible_answer_ends_optional_question_without_losing_new_detail(self):
        for reply in ("I'm open to either", 'Either color works for me',
                      "I don't mind either", 'No strong preference', "I'm flexible"):
            with self.subTest(reply=reply):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'I need a tshirt')
                self.assertEqual(first['receipt']['question']['target_slot'], 'color')
                answer = runtime.chat(sid, reply)
                self.assertEqual(answer['receipt']['conversation_act'], 'declined_options')
                self.assertEqual(answer['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(answer['receipt']['soft'], first['receipt']['soft'])
                self.assertEqual([item['parent_asin'] for item in answer['products']],
                                 [item['parent_asin'] for item in first['products']])
                self.assertIsNone(answer['assistant']['ask_attribute'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

        for reply in ("I don't mind either, but cotton please",
                      'No strong preference, but cotton please'):
            with self.subTest(reply=reply):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                runtime.chat(sid, 'I need a tshirt')
                answer = runtime.chat(sid, reply)
                self.assertTrue(answer['receipt']['declined_options'])
                self.assertEqual(answer['receipt']['hard']['material'], 'cotton')
                self.assertIsNone(answer['assistant']['ask_attribute'])
                undone = runtime.chat(sid, 'Undo')
                self.assertNotIn('material', undone['receipt']['hard'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_accepting_options_can_also_add_an_independent_requirement(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        result = runtime.chat(sid, 'Either is fine, but cotton please')
        self.assertEqual(result['receipt']['hard']['material'], 'cotton')
        self.assertNotIn('color', result['receipt']['hard'])
        self.assertNotIn('color', result['receipt']['soft'])
        self.assertIn('choices can stay open', result['assistant']['message'])
        self.assertIsNone(runtime.agent.memory.pending[sid])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unrecognized_short_reply_does_not_poison_a_bounded_slot(self):
        from intent_router.turn_router import TurnIntentRouter
        question = {'target_slot': 'color', 'constraint_type': 'soft',
                    'options': ['black', 'white'],
                    'option_labels': {'black': 'black', 'white': 'white'}}
        parsed = TurnIntentRouter().understand_turn('banana', pending_question=question)
        self.assertFalse(any(update.slot == 'color' and 'banana' in update.values
                             for update in parsed.slot_updates))
        green = TurnIntentRouter().understand_turn('green', pending_question=question)
        self.assertTrue(any(update.slot == 'color' and 'green' in update.values
                            for update in green.slot_updates))
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        self.assertTrue(first['receipt']['question']['options'])
        reply = runtime.chat(sid, 'banana')
        self.assertEqual(reply['receipt']['conversation_act'], 'unmatched_option')
        self.assertEqual([item['parent_asin'] for item in reply['products']],
                         [item['parent_asin'] for item in first['products']])
        self.assertIsNone(runtime.agent.memory.pending[sid])
        self.assertNotIn('banana', str(reply['receipt']['hard']))
        self.assertNotIn('banana', str(reply['receipt']['soft']))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unsure_category_answer_keeps_product_target_open(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'Can you help me shop?')
        self.assertEqual(first['receipt']['question']['target_slot'], 'category')
        self.assertEqual(first['receipt']['question']['options'], ['t-shirt', 'dress', 'shoes'])
        unsure = runtime.chat(sid, 'whatever')
        self.assertNotIn('category', unsure['receipt']['hard'])
        self.assertEqual(unsure['products'], [])
        self.assertIn('shirt', unsure['assistant']['message'].lower())
        self.assertEqual(unsure['receipt']['suggested_replies'], ['T-shirt', 'Dress', 'Shoes'])
        resumed = runtime.chat(sid, 'black cotton tshirt')
        self.assertEqual(resumed['receipt']['hard']['category'], 't-shirt')
        self.assertEqual(resumed['receipt']['hard']['color'], 'black')
        self.assertEqual(resumed['receipt']['hard']['material'], 'cotton')
        self.assertTrue(resumed['products'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_suggested_category_choice_is_a_normal_search_not_a_literal_label(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'Can you help me shop?')
        self.assertIn('T-shirt', first['receipt']['question']['option_labels'].values())
        second = runtime.chat(sid, 'T-shirt')
        self.assertEqual(second['receipt']['hard']['category'], 't-shirt')
        self.assertTrue(second['products'])

    def test_many_identical_products_do_not_justify_a_question(self):
        runtime = self.runtime(uniform=True)
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a tshirt')
        self.assertEqual(result['receipt']['post_reason'], 'no_valuable_question')
        self.assertIsNone(result['assistant']['ask_attribute'])
        self.assertTrue(result['products'])

    def test_none_of_these_work_is_feedback_not_a_work_use_case(self):
        for message in ('None of these work for me', 'None of these work',
                        "These don't work for me", 'None of these would work for me'):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'I need a tshirt')
                feedback = runtime.chat(sid, message)
                self.assertNotIn('use_case', feedback['receipt']['hard'])
                self.assertNotIn('use_case', feedback['receipt']['soft'])
                self.assertEqual(feedback['receipt']['state_changes'], [])
                self.assertTrue(feedback['receipt']['negative_feedback'])
                self.assertTrue(feedback['products'])
                self.assertFalse({item['parent_asin'] for item in first['products']} &
                                 {item['parent_asin'] for item in feedback['products']})
                self.assertIn('didn\'t work', feedback['assistant']['message'])
                self.assertNotIn("I've pulled together", feedback['assistant']['message'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_batch_feedback_can_include_a_real_requirement(self):
        for message in ("These don't work for me; I need cotton",
                        'None of these work for me, but I need it for work'):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'I need a tshirt')
                feedback = runtime.chat(sid, message)
                expected_slot = 'use_case' if 'for work' in message else 'material'
                self.assertIn(expected_slot,
                              feedback['receipt']['hard'] | feedback['receipt']['soft'])
                self.assertTrue(feedback['products'])
                self.assertFalse({item['parent_asin'] for item in first['products']} &
                                 {item['parent_asin'] for item in feedback['products']})
                self.assertIn("didn't work", feedback['assistant']['message'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_batch_feedback_does_not_promise_fresh_items_when_exhausted(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 3 - i} for i in range(3)]

        def details(ids):
            return [dict(product_id=item, found=True, title='Cotton T-shirt') for item in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        feedback = runtime.chat(sid, "These don't work for me")
        self.assertEqual([item['parent_asin'] for item in feedback['products']],
                         [item['parent_asin'] for item in first['products']])
        self.assertIn("hasn't turned up a fresh verified option", feedback['assistant']['message'])
        self.assertNotIn('use_case', feedback['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_missing_price_never_gets_relaxed_to_fill_results(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt under $30')
        result = runtime.chat(sid, '先看看')
        self.assertEqual(result['products'], [])
        self.assertEqual(result['receipt']['hard']['price_max'], 30)
        self.assertIsNone(result['assistant']['ask_attribute'])
