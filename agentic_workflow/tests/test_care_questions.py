import unittest

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit
from mvp.product_question import answer_product_question
from mvp.demo import DEMO_CATALOG
from mvp.control_intent import parse_control_intent, plan_compound_turn


class CareQuestionTests(unittest.TestCase):
    def test_multiple_unresolved_questions_do_not_guess_one_topic(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A blue dress')
        result = runtime.chat(sid, 'How much is #99? How do I wash #98?')
        self.assertIn("Which product's price did you mean?", result['assistant']['message'])
        self.assertIn("Which product's care instructions did you mean?", result['assistant']['message'])
        self.assertIsNone(result['receipt']['detail_question'])
        self.assertIsNone(runtime.sessions[sid].pending_detail_attribute)
        self.assertEqual(result['products'], first['products'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_one_unresolved_question_in_multi_detail_can_be_repaired_by_rank(self):
        for message in ('How much is #1? How do I wash #99?',
                        'How do I wash #99? How much is #1?'):
            with self.subTest(message=message):
                runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'A blue dress')
                unresolved = runtime.chat(sid, message)
                self.assertIn("Which product's care instructions did you mean?", unresolved['assistant']['message'])
                self.assertEqual(unresolved['receipt']['detail_question']['attribute'], 'care')
                self.assertIn('#2', unresolved['receipt']['suggested_replies'])
                answer = runtime.chat(sid, 'the second one')
                self.assertEqual(answer['receipt']['pre_reason'], 'product_detail')
                self.assertIn('#2:', answer['assistant']['message'])
                self.assertEqual(runtime.sessions[sid].last_detail_attribute, 'care')
                self.assertEqual(answer['products'], first['products'])
                self.assertIsNone(answer['receipt']['detail_question'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_multi_question_turn_keeps_last_resolved_topic_for_followup(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A blue dress')
        runtime.chat(sid, 'How much is #1? How do I wash #2?')
        self.assertEqual(runtime.sessions[sid].last_detail_attribute, 'care')
        followup = runtime.chat(sid, 'What about the first one?')
        self.assertEqual(followup['receipt']['pre_reason'], 'product_detail')
        self.assertIn('#1:', followup['assistant']['message'])
        self.assertIn('Hand wash only', followup['assistant']['message'])
        self.assertEqual(followup['products'], first['products'])
        runtime.chat(sid, 'How much is #1? How do I wash #99?')
        self.assertIsNone(runtime.sessions[sid].last_detail_attribute)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_compound_topic_resolution_uses_latest_explicit_topic(self):
        plan = plan_compound_turn('How much is #2? What about the first one?', detail_topic='care')
        self.assertEqual([(row.attribute, row.ranks) for row in plan.details],
                         [('price', (2,)), ('price', (1,))])
        self.assertIsNone(plan.requirement_message)
        self.assertIsNone(plan_compound_turn('What about the first one? Change to blue'))

    def test_carried_care_question_binds_before_combined_requirement_change(self):
        records = {'black': ('Black cotton T-shirt', 'Hand wash only'),
                   'blue': ('Blue cotton T-shirt', 'Machine wash cold')}

        def search(query, top_k):
            return [{'product_id': asin, 'score': 2 - index} for index, asin in enumerate(records)]

        def details(ids):
            return [{'product_id': asin, 'found': True, 'title': records[asin][0],
                     'details': {'Care': records[asin][1]}} for asin in ids]

        for message in ('What about the first one, but change to blue?',
                        'Sorry, I meant the first one, but change to blue'):
            with self.subTest(message=message):
                runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                             trace_enabled=True), orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                initial = runtime.chat(sid, 'A black T-shirt')
                runtime.chat(sid, 'How do I wash it?')
                result = runtime.chat(sid, message)
                self.assertIn('Hand wash only', result['assistant']['message'])
                self.assertIn('previous list', result['assistant']['message'])
                self.assertNotIn('Machine wash cold', result['assistant']['message'])
                self.assertEqual(result['receipt']['compound_request']['detail_context']['parent_asin'], 'black')
                self.assertEqual([product['parent_asin'] for product in result['products']], ['blue'])
                undo = runtime.chat(sid, 'Undo')
                self.assertEqual(undo['products'], initial['products'])
                self.assertEqual(undo['receipt']['hard'], initial['receipt']['hard'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_possessive_topic_questions_preserve_the_explicit_reference(self):
        for message, attribute in (("What about #2's material?", 'material'),
                                   ("How about the second one's care instructions?", 'care'),
                                   ("And #2’s cost, please?", 'price')):
            with self.subTest(message=message):
                control = parse_control_intent(message)
                self.assertEqual((control.action, control.ranks, control.attribute),
                                 ('detail', (2,), attribute))

    def test_explicit_topic_switch_replaces_the_topic_carried_to_another_item(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A blue dress')
        runtime.chat(sid, 'How do I wash #2?')
        price = runtime.chat(sid, 'What about its price?')
        self.assertIn(f"#2: The catalog lists ${first['products'][1]['price']:g}", price['assistant']['message'])
        other = runtime.chat(sid, 'And the first one?')
        self.assertIn(f"#1: The catalog lists ${first['products'][0]['price']:g}", other['assistant']['message'])
        fabric = runtime.chat(sid, "What about #2's material?")
        self.assertIn('#2:', fabric['assistant']['message'])
        self.assertEqual(runtime.sessions[sid].last_detail_attribute, 'material')
        self.assertEqual(fabric['receipt']['pre_reason'], 'product_detail')
        self.assertEqual(fabric['products'], first['products'])
        self.assertEqual(fabric['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_care_topic_follows_another_item_and_expires_after_a_new_search(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': 'first', 'score': 2}, {'product_id': 'second', 'score': 1}]

        def details(ids):
            return [{'product_id': asin, 'found': True, 'title': 'Black cotton T-shirt',
                     'details': {'Care': 'Hand wash' if asin == 'first' else 'Machine wash cold'}}
                    for asin in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        initial = runtime.chat(sid, 'A black T-shirt')
        runtime.chat(sid, 'How do I wash #2?')
        runtime.chat(sid, 'What are my preferences?')
        count = len(calls)
        answer = runtime.chat(sid, 'What about the first one?')
        self.assertIn('#1:', answer['assistant']['message'])
        self.assertIn('Hand wash', answer['assistant']['message'])
        self.assertNotIn('Machine wash cold', answer['assistant']['message'])
        self.assertEqual(answer['products'], initial['products'])
        self.assertEqual(len(calls), count)
        runtime.chat(sid, 'How much is #99?')
        price = runtime.chat(sid, 'What about the first one?')
        self.assertIn("does not list a price", price['assistant']['message'])
        self.assertNotIn('Hand wash', price['assistant']['message'])
        runtime.chat(sid, 'Change to blue')
        self.assertIsNone(runtime.sessions[sid].last_detail_attribute)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_conflicting_care_is_flagged_without_choosing_a_washing_method(self):
        for restriction in ('Hand wash only', 'Dry-clean only', 'Do not machine wash'):
            with self.subTest(restriction=restriction):
                answer = answer_product_question({'details': {'Care': restriction},
                                                  'features': ['Machine washable']}, 1, 'care', 'en')
                self.assertIn('may conflict or refer to different variants', answer)
                self.assertIn("can't confirm machine washing", answer)
                self.assertIn(restriction, answer)
                self.assertIn('Machine washable', answer)

    def test_negated_machine_washing_is_not_an_affirmative_conflicting_claim(self):
        for instruction in ('Not machine washable', 'Do not machine wash', 'Never machine wash'):
            with self.subTest(instruction=instruction):
                answer = answer_product_question({'details': {'Care': 'Hand wash only'},
                                                  'features': [instruction]}, 1, 'care', 'en')
                self.assertNotIn('may conflict', answer)
                self.assertIn(instruction, answer)

    def test_care_quotes_do_not_cut_off_late_warnings(self):
        care = 'Machine washable for standard colors. ' + 'See variant instructions. ' * 18 + 'Do not wash the red variant.'
        answer = answer_product_question({'details': {'Care': care}}, 1, 'care', 'en')
        self.assertIn(care, answer)
        oversized = answer_product_question({'details': {'Care': care * 5}}, 1, 'care', 'en')
        self.assertIn('check the full listing for restrictions', oversized)
        self.assertNotIn('Machine washable', oversized)

    def test_hyphenated_care_instructions_are_recognized(self):
        answer = answer_product_question({'features': ['Dry-clean only. Do not tumble-dry.']},
                                         1, 'care', 'en')
        self.assertIn('Dry-clean only. Do not tumble-dry.', answer)

    def test_care_answer_quotes_qualifications_without_inventing_permission(self):
        answer = answer_product_question({'details': {'Care': 'Hand wash only; do not tumble dry'},
                                          'features': ['Do not bleach']}, 1, 'care', 'en')
        self.assertIn('Hand wash only; do not tumble dry', answer)
        self.assertIn('Do not bleach', answer)
        self.assertNotIn('Yes', answer)

    def test_material_does_not_imply_washability(self):
        answer = answer_product_question({'title': 'Pure cotton T-shirt',
                                          'details': {'Care': 'Unknown', 'Material': 'Cotton'}},
                                         1, 'care', 'en')
        self.assertIn("doesn't provide care instructions", answer)
        self.assertNotIn('machine wash', answer)

    def test_natural_care_questions_are_read_only_and_resolve_single_item(self):
        for question in ('How do I wash it?', 'Can I machine wash #1?',
                         'Is the first one machine washable?', 'How should I care for it?',
                         'What about its care?', 'Care instructions, please'):
            with self.subTest(question=question):
                calls = []

                def search(query, top_k):
                    calls.append(query)
                    return [{'product_id': 'one', 'score': 1}]

                def details(ids):
                    return [{'product_id': 'one', 'found': True, 'title': 'Black cotton T-shirt',
                             'details': {'Care': 'Hand wash only'}}]

                runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                             trace_enabled=True), orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'A black T-shirt')
                search_count = len(calls)
                answer = runtime.chat(sid, question)
                self.assertIn('Hand wash only', answer['assistant']['message'])
                self.assertEqual(answer['receipt']['pre_reason'], 'product_detail')
                self.assertEqual(answer['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(answer['products'], first['products'])
                self.assertEqual(len(calls), search_count)
                self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
