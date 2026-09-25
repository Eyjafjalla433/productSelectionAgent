import unittest
from dataclasses import replace

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.server import AgentRuntime
from shopping_agent.policy import missing_detail_question
from shopping_agent.retrieval import category_matches, requirements_from_state


class FlexibleIntentTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        def search(query, top_k):
            self.calls.append((query, top_k))
            return [{'product_id': 'TEE', 'score': 1.5}]
        def details(ids):
            return [{'product_id': 'TEE', 'found': True, 'title': 'Black cotton tshirt',
                     'color': 'black', 'bullet_point': 'Cotton regular fit tee'}]
        self.agent = Agent(search_function=search, details_function=details, trace_enabled=True)
        self.agent.reset('test', {})

    def test_exact_reported_sentence_extracts_everything_and_searches(self):
        result = self.agent.respond('test', 'I want to buy a black tshirt around $30', 1)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints, {'category': 't-shirt', 'color': 'black'})
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)
        self.assertIsNone(result['ask_attribute'])
        self.assertEqual(len(result['recommendations']), 1)
        self.assertTrue(self.calls)
        self.assertIn("doesn't include prices", result['message'])
        self.assertEqual(state.suggestions['clarification_count'], 0)

    def test_long_fragment_and_multisentence_extract_all_slots(self):
        for text in ('A black cotton tshirt in size M around $30 please',
                     "I'm looking for a tshirt. Black cotton, size M, around $30."):
            with self.subTest(text=text):
                parsed = self.agent.router.understand_turn(text)
                sets = {u.slot: u.values for u in parsed.slot_updates if u.operation == 'set'}
                for key, value in {'category': ('t-shirt',), 'color': ('black',),
                                   'material': ('cotton',), 'size': ('m',), 'budget_target': (30.0,)}.items():
                    self.assertEqual(sets[key], value)

    def test_rejected_product_type_does_not_override_new_type(self):
        for message, expected in (
            ('Actually, not a T-shirt, a dress under $50', 'dress'),
            ('Forget the T-shirt, I need a blue dress under $50', 'dress'),
            ('I need a T-shirt, not a dress', 't-shirt'),
        ):
            with self.subTest(message=message):
                parsed = self.agent.router.understand_turn(
                    message, pending_question={'target_slot': 'color', 'constraint_type': 'soft'})
                category_sets = [update.values for update in parsed.slot_updates
                                 if update.slot == 'category' and update.operation == 'set']
                self.assertEqual(category_sets, [(expected,)])
        for message in ("I don't want a dress or a jacket",
                        'Not a T-shirt or a dress', 'Not a T-shirt'):
            with self.subTest(message=message):
                parsed = self.agent.router.understand_turn(message)
                self.assertFalse(any(update.slot == 'category' and update.operation == 'set'
                                     for update in parsed.slot_updates))

    def test_category_correction_interrupts_optional_question_and_can_be_undone(self):
        def search(query, top_k):
            return [{'product_id': f'{category}:{i}', 'score': 48 - i}
                    for category in ('tee', 'dress') for i in range(24)]

        def details(ids):
            return [dict(product_id=key, found=True, price=35,
                         title=(('Black' if int(key.split(':')[1]) < 12 else 'White') + ' T-shirt'
                                if key.startswith('tee:') else 'Blue dress')) for key in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a T-shirt')
        self.assertEqual(first['receipt']['question']['target_slot'], 'color')
        changed = runtime.chat(sid, 'Actually, not a T-shirt, a blue dress')
        self.assertEqual(changed['receipt']['hard']['category'], 'dress')
        self.assertEqual(changed['receipt']['hard']['color'], 'blue')
        self.assertTrue(changed['products'])
        self.assertTrue(all('dress' in item['title'].lower() for item in changed['products']))
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['hard']['category'], 't-shirt')
        self.assertEqual([item['parent_asin'] for item in restored['products']],
                         [item['parent_asin'] for item in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_pending_category_answer_can_supply_many_values(self):
        self.agent.respond('test', 'Hello', 1)
        result = self.agent.respond('test', 'black cotton tshirt around $30', 2)
        self.assertIsNone(result['ask_attribute'])
        self.agent.respond('test', 'regular fit for everyday', 3)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints['color'], 'black')
        self.assertEqual(state.hard_constraints['material'], 'cotton')
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)
        question = missing_detail_question(state, 'feature', 'negative_feedback')
        self.assertNotIn(question.question['target_slot'], {'category', 'color', 'material', 'style', 'use_case', 'budget'})

    def test_gift_opening_keeps_context_and_budget_before_product_type(self):
        first = self.agent.respond('test', 'I need a gift for my dad under $50', 1)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(first['ask_attribute'], 'category')
        self.assertEqual(self.calls, [])
        self.assertEqual(state.hard_constraints, {'price_max': 50.0})
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value, 'gift for dad')
        self.assertIn('your dad under $50', first['message'])
        self.assertEqual(state.pending_question['options'], ['t-shirt', 'shoes', 'bag'])
        second = self.agent.respond('test', 'A black T-shirt', 2)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints['category'], 't-shirt')
        self.assertEqual(state.hard_constraints['color'], 'black')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value, 'gift for dad')
        self.assertNotIn('gift for dad', requirements_from_state(state).soft_preferences)
        self.assertNotIn('gift for dad', self.calls[-1][0])
        self.assertEqual(len(self.calls), 1)  # Context alone is not a reason to retry search.
        self.assertTrue(self.calls)

    def test_gift_is_not_inferred_from_negation(self):
        parsed = self.agent.router.understand_turn('I need a T-shirt, not a gift')
        self.assertFalse(any(u.slot == 'shopping_purpose' and u.operation == 'set' for u in parsed.slot_updates))

    def test_gift_recipient_change_can_be_undone(self):
        self.agent.respond('test', 'A gift for my dad', 1)
        self.agent.respond('test', 'Actually, for my mom instead', 2)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value, 'gift for mom')
        self.agent.respond('test', 'Undo', 3)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value, 'gift for dad')

    def test_category_and_recipient_correction_can_arrive_together(self):
        self.agent.respond('test', 'A gift for my dad', 1)
        self.agent.respond('test', 'Actually, a black T-shirt for my mom instead', 2)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints['category'], 't-shirt')
        self.assertEqual(state.hard_constraints['color'], 'black')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value, 'gift for mom')

    def test_gift_context_survives_product_type_switch_and_can_be_withdrawn(self):
        self.agent.respond('test', 'A gift for my dad', 1)
        self.agent.respond('test', 'A black T-shirt', 2)
        self.agent.respond('test', 'Actually, show me shoes', 3)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints['category'], 'shoes')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value, 'gift for dad')
        self.agent.respond('test', 'Not a gift anymore', 4)
        self.assertNotIn('shopping_purpose', self.agent.memory.snapshot('test').soft_preferences)
        self.agent.respond('test', 'Undo', 5)
        self.assertEqual(self.agent.memory.snapshot('test').soft_preferences['shopping_purpose'][0].value, 'gift for dad')

    def test_gift_purpose_is_acknowledged_without_becoming_product_evidence(self):
        self.agent.respond('test', 'A gift for my dad', 1)
        result = self.agent.respond('test', 'A black T-shirt', 2)
        self.assertIn('gift for your dad', result['message'])
        self.assertNotIn('gift for dad', self.calls[-1][0])

    def test_unsure_gift_shopper_gets_honest_starting_points(self):
        self.agent.respond('test', 'A gift for my dad', 1)
        result = self.agent.respond('test', 'Surprise me', 2)
        self.assertEqual(self.calls, [])
        self.assertIn('gift for your dad', result['message'])
        self.assertIn('needs a product type', result['message'])

    def test_gift_card_does_not_trigger_gift_purpose(self):
        parsed = self.agent.router.understand_turn('I need a gift card')
        self.assertFalse(any(u.slot == 'shopping_purpose' and u.operation == 'set' for u in parsed.slot_updates))

    def test_followup_selects_an_unknown_dimension(self):
        self.agent.respond('test', 'black cotton tshirt regular fit around $30', 1)
        state = self.agent.memory.snapshot('test')
        question = missing_detail_question(state, 'feature', 'negative_feedback')
        self.assertEqual(question.question['target_slot'], 'use_case')
        later = replace(state, turn=7)
        self.assertEqual(missing_detail_question(later, 'feature', 'negative_feedback').question['target_slot'], 'use_case')

    def test_aliases_match_titles_without_taxonomy(self):
        for title in ('Black tshirt', 'Black t-shirt', 'Black t shirt', 'Black tee', 'Black tees'):
            self.assertTrue(category_matches({'title': title}, 't-shirt')[0], title)
        self.assertFalse(category_matches({'title': 'Black dress shirt'}, 't-shirt')[0])

    def test_category_alias_spellings(self):
        for name in ('tshirt', 'tshirts', 't-shirt', 't-shirts', 'tee', 'tees', 't shirt'):
            parsed = self.agent.router.understand_turn(f'black {name} around $30')
            self.assertTrue(any(u.slot == 'category' and u.values == ('t-shirt',) for u in parsed.slot_updates), name)

    def test_explicit_change_can_replace_hard_color_with_preference(self):
        self.agent.respond('test', 'black cotton tshirt around $30', 1)
        self.agent.respond('test', 'Actually, I prefer blue', 2)
        state = self.agent.memory.snapshot('test')
        self.assertNotIn('color', state.hard_constraints)
        self.assertEqual(state.soft_preferences['color'][0].value, 'blue')
        self.assertEqual(state.hard_constraints['material'], 'cotton')
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)
        self.assertNotIn('black', self.calls[-1][0])

    def test_optional_material_demotes_instead_of_excluding(self):
        self.agent.respond('test', 'black cotton tshirt', 1)
        self.agent.respond('test', 'Cotton is optional', 2)
        state = self.agent.memory.snapshot('test')
        self.assertNotIn('material', state.hard_constraints)
        self.assertEqual(state.soft_preferences['material'][0].value, 'cotton')
        self.assertNotIn('material', state.exclusions)
        self.assertEqual(state.hard_constraints['color'], 'black')

    def test_tentative_preference_does_not_silently_erase_hard_color(self):
        self.agent.respond('test', 'black tshirt', 1)
        self.agent.respond('test', 'Maybe blue', 2)
        self.assertEqual(self.agent.memory.snapshot('test').hard_constraints['color'], 'black')

    def test_category_change_retains_target_budget_not_old_product_attributes(self):
        self.agent.respond('test', 'black cotton tshirt around $30', 1)
        self.agent.respond('test', 'Switch to shoes', 2)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints, {'category': 'shoes'})
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)

    def test_chinese_explicit_revision(self):
        self.agent.respond('test', '我想要黑色棉质T恤', 1)
        self.agent.respond('test', '改成蓝色，棉质不是必须', 2)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints['color'], 'blue')
        self.assertNotIn('material', state.hard_constraints)
        self.assertEqual(state.soft_preferences['material'][0].value, 'cotton')

    def test_undo_restores_only_last_requirement_change(self):
        self.agent.respond('test', 'black cotton tshirt around $30', 1)
        self.agent.respond('test', 'Actually, I prefer blue', 2)
        result = self.agent.respond('test', 'Undo the last change', 3)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints['color'], 'black')
        self.assertEqual(state.hard_constraints['material'], 'cotton')
        self.assertNotIn('color', state.soft_preferences)
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)
        self.assertIn('undone', result['message'])
        self.assertEqual(state.turn, 3)
        history_length = len(self.agent.memory.requirement_history['test'])
        self.assertEqual(self.agent.respond('test', 'Undo the last change', 3), result)
        self.assertEqual(len(self.agent.memory.requirement_history['test']), history_length)

    def test_undo_skips_repeated_facts_and_unrelated_messages(self):
        for turn, message in enumerate(['black cotton tshirt', 'Actually, I prefer blue', 'blue', 'thanks', '撤销刚才的修改'], 1):
            self.agent.respond('test', message, turn)
        # "blue" changed soft to hard, so undo that genuine change first.
        state = self.agent.memory.snapshot('test')
        self.assertNotIn('color', state.hard_constraints)
        self.assertEqual(state.soft_preferences['color'][0].value, 'blue')
        self.agent.respond('test', 'undo', 6)
        self.assertEqual(self.agent.memory.snapshot('test').hard_constraints['color'], 'black')

    def test_no_history_undo_is_explicit_and_cannot_pollute_pending_slot(self):
        self.agent.respond('test', 'hello', 1)
        result = self.agent.respond('test', 'undo', 2)
        self.assertIn("isn't an earlier", result['message'])
        self.assertEqual(self.agent.memory.snapshot('test').hard_constraints, {})

    def test_undo_category_switch_restores_product_requirements_and_budget(self):
        self.agent.respond('test', 'black cotton tshirt around $30', 1)
        self.agent.respond('test', 'Switch to shoes', 2)
        self.agent.respond('test', '撤销上一步', 3)
        state = self.agent.memory.snapshot('test')
        self.assertEqual(state.hard_constraints, {'category': 't-shirt', 'color': 'black', 'material': 'cotton'})
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 30)

    def test_undo_history_is_session_scoped_and_released(self):
        self.agent.respond('test', 'black tshirt', 1)
        self.agent.reset('second', {})
        self.agent.respond('second', 'undo', 1)
        self.assertEqual(self.agent.memory.snapshot('second').hard_constraints, {})
        self.agent.drop_session('test')
        self.assertNotIn('test', self.agent.memory.requirement_history)

    def test_runtime_undo_preserves_audit_and_localizes_confirmation(self):
        from mvp.server import AgentRuntime
        from mvp.audit import verify_audit
        runtime = AgentRuntime(self.agent, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, '我想要黑色棉质T恤')
        runtime.chat(sid, '改成蓝色')
        result = runtime.chat(sid, '撤销刚才的修改')
        self.assertEqual(result['receipt']['hard']['color'], 'black')
        self.assertEqual(result['receipt']['requirement_undo'], 'applied')
        self.assertIn('undone', result['assistant']['message'])
        self.assertEqual(result['turn'], 3)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_repeating_same_fact_does_not_create_an_undo_step(self):
        for turn, message in enumerate(['black tshirt', 'blue', 'blue', 'undo'], 1):
            self.agent.respond('test', message, turn)
        self.assertEqual(self.agent.memory.snapshot('test').hard_constraints['color'], 'black')

    def test_product_runtime_continues_past_ten_with_undo_and_selection(self):
        from mvp.server import AgentRuntime
        from mvp.audit import verify_audit
        runtime = AgentRuntime(self.agent, orchestration_mode='adaptive')
        session = runtime.new_session()
        self.assertIsNone(session['max_turns'])
        sid = session['session_id']
        for _ in range(10):
            runtime.chat(sid, 'black cotton tshirt around $30')
        runtime.chat(sid, 'Actually, I prefer blue')
        undone = runtime.chat(sid, 'undo')
        self.assertEqual(undone['turn'], 12)
        self.assertEqual(undone['receipt']['hard']['color'], 'black')
        self.assertIsNone(undone['remaining_turns'])
        runtime.chat(sid, 'Compare #1')
        final = runtime.chat(sid, 'Finalize my selection')
        self.assertEqual(final['turn'], 14)
        self.assertEqual(final['selection_state']['status'], 'finalized')
        self.assertIsNone(final['remaining_turns'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
        self.assertFalse(self.agent.errors)

    def test_clarification_not_disabled_after_two_questions_or_ten_turns(self):
        from shopping_agent.policy import PreRetrievalPolicy
        self.agent.respond('test', 'black tshirt', 1)
        state = self.agent.memory.snapshot('test')
        late = replace(state, turn=12, hard_constraints={'category': 't-shirt', 'price_min':100, 'price_max':50},
                       suggestions={**state.suggestions, 'clarification_count': 4})
        self.assertEqual(self.agent.pre_policy.decide(late).reason, 'contradictory_budget')
        self.assertEqual(PreRetrievalPolicy().decide(late).action, 'retrieve')

    def test_explicit_session_limit_remains_enforced(self):
        from mvp.server import AgentRuntime, ApiError
        self.agent.max_turns = 2
        self.agent.memory.max_turns = 2
        runtime = AgentRuntime(self.agent, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        result = runtime.chat(sid, 'black tshirt')
        self.assertEqual(result['remaining_turns'], 0)
        with self.assertRaises(ApiError):
            runtime.chat(sid, 'blue')

    def test_greeting_is_not_a_category_answer(self):
        self.agent.respond('test', 'hello', 1)
        self.agent.respond('test', 'hello', 2)
        self.assertEqual(self.agent.memory.snapshot('test').hard_constraints, {})
        self.assertEqual(self.calls, [])
