import unittest

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit


class ClarificationEscapeTests(unittest.TestCase):
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

    def test_many_identical_products_do_not_justify_a_question(self):
        runtime = self.runtime(uniform=True)
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a tshirt')
        self.assertEqual(result['receipt']['post_reason'], 'no_valuable_question')
        self.assertIsNone(result['assistant']['ask_attribute'])
        self.assertTrue(result['products'])

    def test_missing_price_never_gets_relaxed_to_fill_results(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt under $30')
        result = runtime.chat(sid, '先看看')
        self.assertEqual(result['products'], [])
        self.assertEqual(result['receipt']['hard']['price_max'], 30)
        self.assertIsNone(result['assistant']['ask_attribute'])
