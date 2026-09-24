import unittest
from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit


class RequirementConfirmationTests(unittest.TestCase):
    def test_redo_restores_requirements_and_original_results(self):
        for command in ('redo', '重做刚才的修改', '还是恢复刚才的修改'):
            runtime, sid = self.make_runtime()
            runtime.chat(sid, 'black cotton tshirt around $30')
            blue = runtime.chat(sid, 'blue instead')
            undone = runtime.chat(sid, 'undo')
            self.assertTrue(undone['receipt']['can_redo_requirements'])
            result = runtime.chat(sid, command)
            self.assertEqual(result['receipt']['requirement_redo'], 'applied')
            self.assertFalse(result['receipt']['can_redo_requirements'])
            self.assertEqual(result['receipt']['hard'], blue['receipt']['hard'])
            self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in blue['products']])
            self.assertNotIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, result['turn'])['events']])
            again = runtime.chat(sid, 'undo')
            self.assertEqual(again['receipt']['hard']['color'], 'black')
            self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_redo_survives_noops_but_new_requirement_invalidates_it(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'blue instead')
        runtime.chat(sid, 'undo')
        for message in ('谢谢', 'black tshirt', 'let me think'):
            result = runtime.chat(sid, message)
            self.assertTrue(result['receipt']['can_redo_requirements'])
        changed = runtime.chat(sid, 'size M')
        self.assertFalse(changed['receipt']['can_redo_requirements'])
        result = runtime.chat(sid, 'redo')
        self.assertEqual(result['receipt']['requirement_redo'], 'empty')
        self.assertEqual(result['receipt']['hard'], changed['receipt']['hard'])
        self.assertNotIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, result['turn'])['events']])

    def test_multiple_undo_redo_and_category_boundaries(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black tshirt around $30')
        runtime.chat(sid, 'blue instead')
        runtime.chat(sid, 'switch to shoes')
        runtime.chat(sid, 'undo')
        runtime.chat(sid, 'undo')
        first = runtime.chat(sid, 'redo')
        self.assertEqual(first['receipt']['hard']['color'], 'blue')
        second = runtime.chat(sid, 'redo')
        self.assertEqual(second['receipt']['hard'], {'category': 'shoes'})
        self.assertEqual(runtime.agent.memory.snapshot(sid).soft_preferences['budget_target'][0].value, 30)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
        runtime.agent.drop_session(sid)
        self.assertNotIn(sid, runtime.agent.memory.requirement_future)

    def test_redo_does_not_call_optional_model(self):
        from unittest.mock import Mock
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'undo')
        enhancer = Mock()
        runtime.agent.requirement_enhancer = enhancer
        runtime.chat(sid, 'redo')
        enhancer.enhance.assert_not_called()

    def test_undo_restores_products_without_search_and_records_provenance(self):
        runtime, sid = self.make_runtime()
        first = runtime.chat(sid, 'black cotton tshirt')
        runtime.chat(sid, 'blue instead')
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['display_mode'], 'restored_previous_results')
        self.assertEqual(restored['receipt']['restored_from_turn'], 1)
        self.assertEqual([p['parent_asin'] for p in restored['products']], [p['parent_asin'] for p in first['products']])
        self.assertNotIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, 3)['events']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
        runtime.agent.drop_session(sid)
        self.assertNotIn(sid, runtime.agent.result_history)

    def test_changed_source_falls_back_to_search_instead_of_restoring(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black cotton tshirt')
        runtime.chat(sid, 'blue instead')
        runtime.agent.retriever.products['black']['title'] = 'Changed catalog record'
        restored = runtime.chat(sid, 'undo')
        self.assertIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, 3)['events']])
        self.assertNotEqual(restored['receipt'].get('display_mode'), 'restored_previous_results')

    def test_rejected_product_is_not_restored_by_requirement_undo(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black cotton tshirt')
        runtime.chat(sid, "I don't like #1")
        runtime.chat(sid, 'blue instead')
        result = runtime.chat(sid, 'undo')
        self.assertNotIn('black', [p['parent_asin'] for p in result['products']])
        self.assertFalse(runtime.agent.errors)

    def test_result_history_is_bounded_and_reset_clears_it(self):
        runtime, sid = self.make_runtime()
        for budget in range(30, 56):
            runtime.chat(sid, f'black tshirt around ${budget}')
        self.assertEqual(len(runtime.agent.result_history[sid]), 24)
        runtime.agent.reset(sid, {})
        self.assertEqual(runtime.agent.result_history[sid], [])

    def test_undo_control_tracks_actual_requirement_history(self):
        runtime, sid = self.make_runtime()
        first = runtime.chat(sid, 'black tshirt')
        self.assertTrue(first['receipt']['can_undo_requirements'])
        paused = runtime.chat(sid, '等一下')
        self.assertTrue(paused['receipt']['can_undo_requirements'])
        restored = runtime.chat(sid, '撤销刚才的修改')
        self.assertFalse(restored['receipt']['can_undo_requirements'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def make_runtime(self):
        def search(query, top_k):
            return [{'product_id': c, 'score': 2-i} for i, c in enumerate(('black', 'blue'))]
        def details(ids):
            return [dict(product_id=c, found=True, title=c+' cotton tshirt (Medium)', color=c) for c in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        return runtime, runtime.new_session()['session_id']

    def propose(self, runtime, sid):
        runtime.chat(sid, 'I need a black cotton tshirt around $30')
        result = runtime.chat(sid, 'Maybe blue')
        self.assertEqual(result['receipt']['hard']['color'], 'black')
        self.assertEqual(result['receipt']['pre_reason'], 'confirm_requirement_change')
        return result

    def test_replace_keep_both_and_skip(self):
        for reply, expected in [('replace', 'blue'), ('换成新的', 'blue'), ('both', ['black','blue']),
                                ('两种都看', ['black','blue']), ('保留原来的', 'black'), ('show me first', 'black')]:
            with self.subTest(reply=reply):
                runtime, sid = self.make_runtime()
                self.propose(runtime, sid)
                result = runtime.chat(sid, reply)
                actual = result['receipt']['hard']['color']
                self.assertEqual(list(actual) if isinstance(actual, tuple) else actual, expected)
                self.assertEqual(result['receipt']['hard']['material'], 'cotton')
                self.assertIsNone(result['assistant']['ask_attribute'])
                self.assertTrue(result['products'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_chinese_tentative_change_asks_not_overwrites(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, '我想要黑色T恤')
        result = runtime.chat(sid, '蓝色也不错')
        self.assertEqual(result['receipt']['hard']['color'], 'black')
        self.assertIn('Replace it with blue', result['assistant']['message'])

    def test_unrecognized_reply_never_becomes_a_color(self):
        runtime, sid = self.make_runtime()
        self.propose(runtime, sid)
        result = runtime.chat(sid, "I'm not sure")
        self.assertEqual(result['receipt']['hard']['color'], 'black')
        self.assertIsNone(result['assistant']['ask_attribute'])

    def test_confirmation_can_be_undone_and_keeps_other_exclusions(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black cotton tshirt, not red')
        runtime.chat(sid, 'Maybe blue')
        result = runtime.chat(sid, 'replace')
        self.assertIn('red', result['receipt']['excluded']['color'])
        result = runtime.chat(sid, 'undo')
        self.assertEqual(result['receipt']['hard']['color'], 'black')
        self.assertIn('red', result['receipt']['excluded']['color'])

    def test_explicit_change_needs_no_extra_confirmation(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black tshirt')
        result = runtime.chat(sid, 'Actually, I prefer blue')
        self.assertNotEqual(result['receipt']['pre_reason'], 'confirm_requirement_change')

    def test_tentative_proposal_does_not_lift_exclusion_before_confirmation(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black cotton tshirt, not blue')
        pending = runtime.chat(sid, 'Maybe blue')
        self.assertIn('blue', pending['receipt']['excluded']['color'])
        accepted = runtime.chat(sid, 'replace')
        self.assertNotIn('blue', accepted['receipt']['excluded'].get('color', []))
        restored = runtime.chat(sid, 'undo')
        self.assertIn('blue', restored['receipt']['excluded']['color'])

    def test_confirmation_and_extra_requirements_are_one_undoable_change(self):
        for reply in ('replace, size M, around $40', '换成新的，尺码M，预算还是40左右',
                      'replace and size M, around $40'):
            with self.subTest(reply=reply):
                runtime, sid = self.make_runtime()
                self.propose(runtime, sid)
                result = runtime.chat(sid, reply)
                self.assertEqual(result['receipt']['hard']['color'], 'blue')
                self.assertEqual(result['receipt']['hard']['size'], 'm')
                self.assertTrue(result['products'])
                state = runtime.agent.memory.snapshot(sid)
                self.assertEqual(state.soft_preferences['budget_target'][0].value, 40)
                restored = runtime.chat(sid, 'undo')
                self.assertEqual(restored['receipt']['hard']['color'], 'black')
                self.assertNotIn('size', restored['receipt']['hard'])
                self.assertEqual(runtime.agent.memory.snapshot(sid).soft_preferences['budget_target'][0].value, 30)
                self.assertFalse(runtime.agent.errors)
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_keep_or_both_can_also_change_another_attribute(self):
        for reply, color in [('keep original, linen instead', 'black'),
                             ('两种都看，换成亚麻', ('black','blue'))]:
            with self.subTest(reply=reply):
                runtime, sid = self.make_runtime()
                self.propose(runtime, sid)
                result = runtime.chat(sid, reply)
                self.assertEqual(result['receipt']['hard']['color'], color)
                self.assertEqual(result['receipt']['hard']['material'], 'linen')

    def test_skip_with_details_keeps_conditions_and_exits_questions(self):
        runtime, sid = self.make_runtime()
        self.propose(runtime, sid)
        result = runtime.chat(sid, '先看看，但尺码M，预算还是30左右')
        self.assertEqual(result['receipt']['hard']['color'], 'black')
        self.assertEqual(result['receipt']['hard']['size'], 'm')
        self.assertEqual(result['receipt']['post_reason'], 'user_requested_results')
        self.assertIsNone(result['assistant']['ask_attribute'])

    def test_approximate_chinese_budget_never_becomes_a_hard_cap(self):
        runtime, sid = self.make_runtime()
        result = runtime.chat(sid, '我想要黑色T恤，尺码M，预算30左右')
        self.assertNotIn('price_max', result['receipt']['hard'])
        self.assertEqual(runtime.agent.memory.snapshot(sid).soft_preferences['budget_target'][0].value, 30)
        slots = runtime.agent.router.understand('under $20, 预算30左右').slots
        self.assertEqual(slots['budget_max'], 20)
        self.assertEqual(slots['budget_target'], 30)

    def test_size_filter_uses_explicit_size_evidence(self):
        from shopping_agent.retrieval import size_matches
        self.assertTrue(size_matches({'title': 'Cotton tee (Medium)'}, 'm'))
        self.assertTrue(size_matches({'title': 'Cotton tee', 'details': {'Size': 'M'}}, 'm'))
        self.assertFalse(size_matches({'title': 'M logo cotton tee'}, 'm'))
        self.assertFalse(size_matches({'title': 'Cotton tee (Small)'}, 'm'))
        self.assertFalse(size_matches({'title': 'Cotton tee'}, 'm'))
        self.assertFalse(size_matches({'title': 'Cotton tee Extra Large'}, 'l'))
        self.assertFalse(size_matches({'title': '30/1 cotton tee'}, '30'))

    def test_pause_preserves_confirmation_without_searching_or_reasking(self):
        runtime, sid = self.make_runtime()
        self.propose(runtime, sid)
        before = runtime.agent.memory.snapshot(sid)
        pending = runtime.agent.memory.pending[sid].copy()
        result = runtime.chat(sid, '等一下')
        after = runtime.agent.memory.snapshot(sid)
        self.assertEqual(runtime.agent.memory.pending[sid], pending)
        self.assertEqual(before.suggestions['clarification_count'], after.suggestions['clarification_count'])
        self.assertEqual(before.suggestions['clarification_streak'], after.suggestions['clarification_streak'])
        self.assertIsNone(result['assistant']['ask_attribute'])
        self.assertIn('Take your time', result['assistant']['message'])
        stages = [e['stage'] for e in runtime.agent.get_trace(sid, result['turn'])['events']]
        self.assertNotIn('2_retrieval', stages)
        accepted = runtime.chat(sid, '换成新的')
        self.assertEqual(accepted['receipt']['hard']['color'], 'blue')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_thanks_retains_results_instead_of_refreshing_them(self):
        runtime, sid = self.make_runtime()
        first = runtime.chat(sid, 'black tshirt')
        second = runtime.chat(sid, '谢谢')
        self.assertEqual([p['parent_asin'] for p in first['products']], [p['parent_asin'] for p in second['products']])
        self.assertEqual(second['receipt']['display_mode'], 'retained_previous_results')
        self.assertEqual(second['receipt']['new_product_count'], 0)
        self.assertIn('welcome', second['assistant']['message'])
        self.assertIsNone(second['assistant']['ask_attribute'])
        self.assertNotIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, 2)['events']])

    def test_pause_does_not_invalidate_finalized_selection(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Compare #1')
        runtime.chat(sid, 'Finalize my selection')
        result = runtime.chat(sid, 'let me think')
        self.assertEqual(result['selection_state']['status'], 'finalized')
        self.assertFalse(result['receipt']['selection_finalization_invalidated'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_acknowledgement_does_not_call_optional_model(self):
        from unittest.mock import Mock
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black tshirt')
        enhancer = Mock()
        runtime.agent.requirement_enhancer = enhancer
        runtime.chat(sid, 'thanks')
        enhancer.enhance.assert_not_called()

    def test_thanks_with_new_details_is_not_swallowed(self):
        runtime, sid = self.make_runtime()
        runtime.chat(sid, 'black tshirt')
        result = runtime.chat(sid, 'thanks, blue instead')
        self.assertEqual(result['receipt']['hard']['color'], 'blue')
        self.assertFalse(result['receipt'].get('conversation_act'))

    def test_confirmation_retains_current_results_when_requirements_unchanged(self):
        runtime, sid = self.make_runtime()
        first = runtime.chat(sid, 'black tshirt')
        question = runtime.chat(sid, 'Maybe blue')
        self.assertEqual(question['receipt']['display_mode'], 'retained_previous_results')
        self.assertEqual([p['parent_asin'] for p in question['products']], [p['parent_asin'] for p in first['products']])
        self.assertIsNotNone(question['assistant']['ask_attribute'])
        paused = runtime.chat(sid, '等一下')
        self.assertEqual([p['parent_asin'] for p in paused['products']], [p['parent_asin'] for p in first['products']])
