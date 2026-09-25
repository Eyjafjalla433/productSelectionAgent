import unittest

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit
from mvp.control_intent import plan_compound_turn, plan_selection_and_requirements, plan_shortlist_actions, split_detail_and_requirements


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

    def test_update_then_answer_previous_item_and_undo_one_turn(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        message = 'Change to blue, and is #2 pure cotton?'
        result = runtime.chat(sid, message)
        self.assertEqual(result['turn'], 2)
        self.assertIn('previous list', result['assistant']['message'])
        self.assertIn('#2: Yes.', result['assistant']['message'])
        self.assertEqual(result['receipt']['compound_request']['detail_context']['parent_asin'], first['products'][1]['parent_asin'])
        self.assertEqual(result['receipt']['compound_request']['requirements_message'], 'Change to blue')
        self.assertEqual(result['receipt']['hard']['color'], 'blue')
        self.assertTrue(result['products'])
        self.assertTrue(all('Blue' in p['title'] for p in result['products']))
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard']['color'], 'black')
        self.assertEqual([p['parent_asin'] for p in restored['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(restored['receipt']['focused_product_id'], first['products'][1]['parent_asin'])
        followup = runtime.chat(sid, 'Is it pure cotton?')
        self.assertIn('#2: Yes.', followup['assistant']['message'])
        self.assertEqual(followup['receipt']['pre_reason'], 'product_detail')
        self.assertEqual(followup['receipt']['new_product_count'], 0)
        audit = runtime.audit(sid)
        self.assertEqual(audit['turns'][1]['user_message'], message)
        self.assertEqual(verify_audit(audit), [])

    def test_undo_redo_restores_focus_for_each_displayed_list(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        black = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'What material is the second one made of?')
        blue = runtime.chat(sid, 'Change to blue')
        runtime.chat(sid, 'How much does #1 cost?')
        restored_black = runtime.chat(sid, 'undo')
        self.assertEqual(restored_black['receipt']['focused_product_id'], black['products'][1]['parent_asin'])
        self.assertIn('#2: Yes.', runtime.chat(sid, 'Is it pure cotton?')['assistant']['message'])
        restored_blue = runtime.chat(sid, 'redo')
        self.assertEqual(restored_blue['receipt']['focused_product_id'], blue['products'][0]['parent_asin'])
        self.assertIn('#1: Yes.', runtime.chat(sid, 'Is it pure cotton?')['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_undo_does_not_resurrect_cleared_reference(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'What material is the second one made of?')
        runtime.chat(sid, 'How much does #99 cost?')
        runtime.chat(sid, 'Change to blue')
        restored = runtime.chat(sid, 'undo')
        self.assertIsNone(restored['receipt']['focused_product_id'])
        followup = runtime.chat(sid, 'Is it pure cotton?')
        self.assertIn('Which product do you mean?', followup['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_size_fragment_and_detail_question_share_one_turn(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]
        def details(ids):
            return [dict(product_id=i, found=True, title='Black cotton tshirt size M',
                         bullet_point='100% cotton') for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        message = 'Size M, and is #2 pure cotton?'
        reply = runtime.chat(sid, message)
        self.assertEqual(reply['turn'], 2)
        self.assertEqual(reply['receipt']['hard']['size'], 'm')
        self.assertEqual(reply['receipt']['compound_request']['requirements_message'], 'Size M')
        self.assertEqual(reply['receipt']['compound_request']['detail_context']['parent_asin'], first['products'][1]['parent_asin'])
        self.assertIn('previous list', reply['assistant']['message'])
        self.assertIn('#2: Yes.', reply['assistant']['message'])
        self.assertEqual(runtime.audit(sid)['turns'][1]['user_message'], message)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_question_then_undo_binds_question_to_pre_undo_list(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        blue = runtime.chat(sid, 'Change to blue')
        message = 'Undo, and is #2 pure cotton?'
        reply = runtime.chat(sid, message)
        self.assertEqual(reply['receipt']['requirement_undo'], 'applied')
        self.assertEqual(reply['receipt']['hard']['color'], 'black')
        self.assertEqual(reply['receipt']['compound_request']['detail_context']['parent_asin'], blue['products'][1]['parent_asin'])
        self.assertIn('Blue cotton tshirt', reply['assistant']['message'])
        self.assertIn('#2: Yes.', reply['assistant']['message'])
        self.assertEqual(runtime.audit(sid)['turns'][2]['user_message'], message)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_connector_preserves_multislot_tail(self):
        control, tail = split_detail_and_requirements('What material is the second one made of? Also change to blue, under $30')
        self.assertEqual(control.ranks, (2,))
        self.assertEqual(tail, 'change to blue, under $30')
        self.assertIsNone(split_detail_and_requirements('第二款是什么材质'))
        self.assertIsNone(split_detail_and_requirements('第二款多少钱？另外确认选择'))

    def test_requirement_first_split_is_bounded(self):
        for message, expected in (
            ('Change to blue, and is #2 pure cotton?', 'Change to blue'),
            ('Blue instead. Also, what material is the second one made of?', 'Blue instead'),
            ('Change to blue and is #2 pure cotton?', 'Change to blue'),
            ('Change to blue, under $30, and is #2 pure cotton?', 'Change to blue, under $30'),
            ('Size M, and is #2 pure cotton?', 'Size M'),
            ('Undo, and is #2 pure cotton?', 'Undo'),
        ):
            with self.subTest(message=message):
                control, requirement = split_detail_and_requirements(message)
                self.assertEqual(control.action, 'detail')
                self.assertEqual(control.ranks, (2,))
                self.assertEqual(requirement, expected)
        self.assertIsNone(split_detail_and_requirements('I want blue and cotton'))
        self.assertIsNone(split_detail_and_requirements('Compare #1 and #2, and is #2 pure cotton?'))

    def test_two_questions_then_preference_change_bind_to_previous_list(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        black = runtime.chat(sid, 'black tshirt')
        message = 'Is #2 pure cotton? How much does #3 cost? Also change to blue'
        reply = runtime.chat(sid, message)
        self.assertIn('#2: Yes.', reply['assistant']['message'])
        self.assertIn('#3:', reply['assistant']['message'])
        self.assertEqual(reply['receipt']['hard']['color'], 'blue')
        contexts = reply['receipt']['compound_request']['detail_contexts']
        self.assertEqual([item['parent_asin'] for item in contexts],
                         [black['products'][1]['parent_asin'], black['products'][2]['parent_asin']])
        self.assertEqual([item['attribute'] for item in contexts], ['material_check:pure cotton', 'price'])
        self.assertEqual(runtime.audit(sid)['turns'][1]['user_message'], message)
        restored = runtime.chat(sid, 'undo')
        self.assertEqual([p['parent_asin'] for p in restored['products']],
                         [p['parent_asin'] for p in black['products']])
        self.assertEqual(restored['receipt']['focused_product_id'], black['products'][2]['parent_asin'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_two_questions_without_update_are_read_only_and_keep_focus(self):
        calls = []
        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]
        def details(ids):
            return [dict(product_id=i, found=True, title='Black cotton tshirt',
                         bullet_point='100% cotton') for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        searches = len(calls)
        reply = runtime.chat(sid, 'Is #2 pure cotton? How much is it?')
        self.assertEqual(len(calls), searches)
        self.assertEqual(reply['receipt']['pre_reason'], 'product_detail')
        self.assertEqual(reply['receipt']['state_changes'], [])
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertEqual([item['rank'] for item in reply['receipt']['compound_request']['detail_contexts']], [2, 2])
        self.assertIn('#2: Yes.', reply['assistant']['message'])
        self.assertIn('does not list a price', reply['assistant']['message'].lower())
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_requirement_first_multi_question_plan(self):
        plan = plan_compound_turn('Change to blue, and is #2 pure cotton? Also how much does #3 cost?')
        self.assertIsNotNone(plan)
        self.assertEqual(plan.requirement_message, 'Change to blue')
        self.assertEqual([control.ranks for control in plan.details], [(2,), (3,)])

    def test_keep_previous_item_and_refine_search_in_one_turn(self):
        for message in ('I like #2, but show me blue ones',
                        'Keep #2 and change to blue', 'Change to blue and keep #2'):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                black = runtime.chat(sid, 'black tshirt')
                old_item = black['products'][1]['parent_asin']
                reply = runtime.chat(sid, message)
                self.assertEqual(reply['receipt']['hard']['color'], 'blue')
                self.assertEqual(reply['selection_state']['selected_asins'], [old_item])
                self.assertEqual(reply['receipt']['selection_added_from_previous_list'], [2])
                self.assertEqual(reply['receipt']['compound_request']['selection_context'][0]['parent_asin'], old_item)
                self.assertIn('Kept #2 from the previous list.', reply['assistant']['message'])
                self.assertTrue(all('Blue' in p['title'] for p in reply['products']))
                unselected = runtime.chat(sid, 'undo selection')
                self.assertEqual(unselected['selection_state']['selected_asins'], [])
                self.assertEqual(unselected['receipt']['hard']['color'], 'blue')
                restored = runtime.chat(sid, 'undo')
                self.assertEqual(restored['receipt']['hard']['color'], 'black')
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unsupported_mixed_shortlist_command_does_not_silently_drop_refinement(self):
        self.assertEqual(plan_selection_and_requirements('Compare #1 and #2, then show me blue ones')[0].action,
                         'compare')
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        black = runtime.chat(sid, 'black tshirt')
        reply = runtime.chat(sid, 'Compare #1 and #2, then show me blue ones')
        self.assertEqual(reply['receipt']['hard']['color'], 'black')
        self.assertEqual(reply['selection_state']['selected_asins'], [])
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in black['products']])
        self.assertIn("I heard more than one action", reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_keep_and_change_category_requires_explicit_order(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        reply = runtime.chat(sid, 'Keep #2 and switch to shoes')
        self.assertEqual(reply['receipt']['hard']['category'], 't-shirt')
        self.assertEqual(reply['selection_state']['selected_asins'], [])
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertIn('Which should I do first?', reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_multiple_shortlist_verbs_bind_only_their_own_ranks(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #3')
        reply = runtime.chat(sid, 'Keep #2 and remove #3')
        self.assertEqual(reply['selection_state']['selected_asins'], [first['products'][1]['parent_asin']])
        self.assertEqual(reply['receipt']['compound_request']['shortlist_actions'],
                         [{'action': 'select', 'ranks': [2]}, {'action': 'remove', 'ranks': [3]}])
        self.assertIn('Kept #2', reply['assistant']['message'])
        self.assertIn('Removed #3', reply['assistant']['message'])
        self.assertEqual(reply['receipt']['hard']['color'], 'black')
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        undone = runtime.chat(sid, 'undo selection')
        self.assertEqual(undone['selection_state']['selected_asins'], [first['products'][2]['parent_asin']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_not_third_means_not_shortlisted_not_rejected(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #3')
        reply = runtime.chat(sid, 'I like #2 but not #3')
        self.assertEqual(reply['selection_state']['selected_asins'], [first['products'][1]['parent_asin']])
        self.assertEqual(reply['selection_state']['rejected_asins'], [])
        self.assertEqual([step.action for step in plan_shortlist_actions('I like #2 but not #3')],
                         ['select', 'remove'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_dislike_rejects_only_its_own_rank(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        reply = runtime.chat(sid, "Keep #2 and I don't like #3")
        self.assertEqual(reply['selection_state']['selected_asins'], [first['products'][1]['parent_asin']])
        self.assertEqual(reply['selection_state']['rejected_asins'], [first['products'][2]['parent_asin']])
        self.assertEqual(reply['receipt']['compound_request']['shortlist_actions'],
                         [{'action': 'select', 'ranks': [2]}, {'action': 'reject', 'ranks': [3]}])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unhandled_shortlist_mix_does_not_apply_a_broad_regex(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        for message in ("Choose #1 and don't select #2", 'Keep #2 and clear shortlist'):
            with self.subTest(message=message):
                reply = runtime.chat(sid, message)
                self.assertEqual(reply['selection_state']['selected_asins'], [])
                self.assertEqual([p['parent_asin'] for p in reply['products']],
                                 [p['parent_asin'] for p in first['products']])
                self.assertIn("I haven't changed your shortlist or search", reply['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_reject_then_show_more_runs_as_one_grounded_turn(self):
        for message in ('Reject #2 and show me more', 'I do not like #2, show me more'):
            with self.subTest(message=message):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'tshirt')
                old_item = first['products'][1]['parent_asin']
                reply = runtime.chat(sid, message)
                self.assertEqual(reply['turn'], 2)
                self.assertEqual(reply['receipt']['compound_request']['selection_context'][0]['parent_asin'], old_item)
                self.assertEqual(reply['receipt']['rejected_from_previous_list'], [2])
                self.assertIn(old_item, reply['selection_state']['rejected_asins'])
                self.assertNotIn(old_item, [product['parent_asin'] for product in reply['products']])
                self.assertIn("I'll leave #2 out", reply['assistant']['message'])
                stages = [event['stage'] for event in runtime.agent.get_trace(sid, 2)['events']]
                self.assertIn('0_product_feedback', stages)
                self.assertIn('2_retrieval', stages)
                self.assertEqual(runtime.audit(sid)['turns'][1]['user_message'], message)
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_reject_and_failed_search_still_reports_what_was_saved(self):
        calls = []
        def search(query, top_k):
            calls.append(query)
            if len(calls) == 2:
                raise TimeoutError('temporary search failure')
            return [{'product_id': str(i), 'score': 20-i} for i in range(12)]
        def details(ids):
            return [dict(product_id=i, found=True, title='Cotton tshirt') for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        old_item = first['products'][1]['parent_asin']
        reply = runtime.chat(sid, 'Reject #2 and show me more')
        self.assertEqual(reply['turn'], 2)
        self.assertIn(old_item, reply['selection_state']['rejected_asins'])
        self.assertIn("I'll leave #2 out", reply['assistant']['message'])
        self.assertFalse(reply['products'])
        retried = runtime.chat(sid, 'retry')
        self.assertTrue(retried['products'])
        self.assertNotIn(old_item, [product['parent_asin'] for product in retried['products']])
        self.assertIn(old_item, retried['selection_state']['rejected_asins'])
        restored = runtime.chat(sid, 'undo rejection')
        self.assertNotIn(old_item, restored['selection_state']['rejected_asins'])
        self.assertEqual([p['parent_asin'] for p in restored['products']],
                         [p['parent_asin'] for p in retried['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
