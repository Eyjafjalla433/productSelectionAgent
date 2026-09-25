import unittest

from agentic_workflow import Agent
from mvp.control_intent import parse_control_intent, parse_comparison_reference
from mvp.server import AgentRuntime
from mvp.demo import DEMO_CATALOG
from agentic_workflow.runtime import create_runtime
from mvp.audit import verify_audit


class NaturalComparisonTests(unittest.TestCase):
    def test_pending_comparison_survives_a_detail_followup_and_correction(self):
        calls = []

        def search(query, top_k):
            calls.append((query, top_k))
            return [{'product_id': str(i), 'score': 4-i} for i in range(4)]

        def details(ids):
            return [{'product_id': asin, 'found': True, 'title': 'Blue cotton dress',
                     'details': {'Care': 'Hand wash only' if asin == '0' else 'Machine wash cold'}}
                    for asin in ids]

        runtime = create_runtime(search_function=search, details_function=details)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A blue dress')
        pending = runtime.chat(sid, 'I prefer cotton, compare these two')
        search_count = len(calls)
        for message, rank, care in (('How do I wash #2?', 2, 'Machine wash cold'),
                                    ('What about the first one?', 1, 'Hand wash only'),
                                    ('Sorry, I meant the third one', 3, 'Machine wash cold')):
            with self.subTest(message=message):
                reply = runtime.chat(sid, message)
                self.assertIn(f'#{rank}:', reply['assistant']['message'])
                self.assertIn(care, reply['assistant']['message'])
                self.assertEqual(reply['receipt']['comparison_reference_question']['pending_requirements'],
                                 'I prefer cotton')
                self.assertEqual(reply['receipt']['soft'], pending['receipt']['soft'])
                self.assertEqual(reply['products'], first['products'])
                self.assertEqual(reply['selection_state']['selected_asins'], [])
                self.assertEqual(len(calls), search_count)
        resumed = runtime.chat(sid, 'Compare #1 and #2')
        self.assertEqual(resumed['receipt']['soft']['material'], ['cotton'])
        self.assertEqual(resumed['handoff']['comparison_assist']['selected_asins'], ['0', '1'])
        self.assertIsNone(resumed['receipt'].get('comparison_reference_question'))
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('material', undone['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_oversized_leading_group_can_be_reduced_without_silent_truncation(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A blue dress')
        too_many = runtime.chat(sid, 'Compare the first four')
        self.assertIn('up to three', too_many['assistant']['message'])
        self.assertEqual(too_many['selection_state']['selected_asins'], [])
        self.assertNotIn('handoff', too_many)
        repaired = runtime.chat(sid, 'the top two, please')
        self.assertEqual(repaired['handoff']['comparison_assist']['selected_asins'],
                         [product['parent_asin'] for product in first['products'][:2]])

    def test_first_two_is_a_group_not_only_the_first_ordinal(self):
        for message, expected in (('Compare the first two', (1, 2)),
                                  ('Please compare the top 3 products', (1, 2, 3)),
                                  ('Compare the first four', (1, 2, 3, 4))):
            with self.subTest(message=message):
                self.assertEqual(parse_control_intent(message).ranks, expected)
        self.assertEqual(parse_comparison_reference('the top two, please').ranks, (1, 2))
        self.assertIsNone(parse_comparison_reference('not the first two'))
        self.assertIsNone(parse_comparison_reference('maybe the top two'))

    def test_polite_rank_replies_preserve_order_and_invalid_references(self):
        for message, ranks in (('the first and second, please', (1, 2)),
                               ('Please #3 and #2', (3, 2)),
                               ('#1 and #99 please', (1, 99))):
            with self.subTest(message=message):
                self.assertEqual(parse_comparison_reference(message).ranks, ranks)
        for message in ('not #1 and #2 please', 'maybe #1 and #2', '#1 and #2, I prefer linen'):
            with self.subTest(message=message):
                self.assertIsNone(parse_comparison_reference(message))

    def test_these_two_uses_only_an_unambiguous_displayed_pair(self):
        for count in (2, 4):
            with self.subTest(count=count):
                def search(query, top_k):
                    return [{'product_id': str(i), 'score': count-i} for i in range(count)]

                def details(ids):
                    return [{'product_id': asin, 'found': True, 'title': 'Blue cotton dress'} for asin in ids]

                runtime = create_runtime(search_function=search, details_function=details)
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'A blue dress')
                runtime.chat(sid, 'Keep #1 and #2')
                answer = runtime.chat(sid, 'How do these two differ?')
                self.assertEqual(answer['products'], first['products'])
                if count == 2:
                    self.assertEqual(answer['handoff']['comparison_assist']['selected_asins'], ['0', '1'])
                    self.assertEqual(answer['handoff']['comparison_scope'], 'requested_products')
                else:
                    self.assertIn('Which two displayed items', answer['assistant']['message'])
                    self.assertNotIn('handoff', answer)
                    repaired = runtime.chat(sid, '#3 and #2')
                    self.assertEqual(repaired['handoff']['comparison_assist']['selected_asins'], ['2', '1'])
                    mixed = runtime.chat(sid, 'I prefer cotton, compare these two')
                    self.assertIn("haven't changed your preferences yet", mixed['assistant']['message'])
                    self.assertIn('Which two displayed items', mixed['assistant']['message'])
                    self.assertNotIn('material', mixed['receipt']['soft'])
                    self.assertEqual(mixed['receipt']['comparison_reference_question']['pending_requirements'], 'I prefer cotton')
                    self.assertEqual(mixed['receipt']['suggested_replies'], ['Never mind'])
                    self.assertNotIn('handoff', mixed)
                    self.assertEqual(mixed['products'], first['products'])
                    self.assertEqual(mixed['selection_state']['selected_asins'], repaired['selection_state']['selected_asins'])
                    unsure = runtime.chat(sid, "I'm not sure yet")
                    self.assertEqual(unsure['receipt']['comparison_reference_question']['pending_requirements'], 'I prefer cotton')
                    self.assertEqual(unsure['receipt']['soft'], mixed['receipt']['soft'])
                    self.assertEqual(unsure['products'], mixed['products'])
                    self.assertEqual(unsure['receipt']['suggested_replies'], ['Never mind'])
                    recap = runtime.chat(sid, 'What are my preferences?')
                    self.assertIn('I prefer cotton', recap['assistant']['message'])
                    self.assertIn("isn't saved yet", recap['assistant']['message'])
                    detour = runtime.chat(sid, 'How much is #2?')
                    self.assertIn('#2:', detour['assistant']['message'])
                    self.assertTrue(runtime.sessions[sid].pending_compare_reference)
                    self.assertEqual(runtime.sessions[sid].pending_comparison_requirements, 'I prefer cotton')
                    invalid = runtime.chat(sid, '#1 and #99')
                    self.assertNotIn('material', invalid['receipt']['soft'])
                    resumed = runtime.chat(sid, 'Compare the first two, please')
                    self.assertEqual(resumed['receipt']['soft']['material'], ['cotton'])
                    self.assertEqual(resumed['handoff']['comparison_assist']['selected_asins'], ['0', '1'])
                    self.assertFalse(resumed['receipt'].get('comparison_reference_question'))
                    self.assertIsNone(runtime.sessions[sid].pending_comparison_requirements)
                    undone = runtime.chat(sid, 'Undo')
                    self.assertNotIn('material', undone['receipt']['soft'])
                    runtime.chat(sid, 'I prefer cotton, compare these two')
                    replacement = runtime.chat(sid, 'Compare #1 and #2, I prefer linen')
                    self.assertEqual(replacement['receipt']['soft']['material'], ['linen'])
                    self.assertIsNone(runtime.sessions[sid].pending_comparison_requirements)
                    self.assertFalse(runtime.sessions[sid].pending_compare_reference)
                    self.assertFalse(replacement['receipt'].get('comparison_reference_question'))
                    runtime.chat(sid, 'I prefer cotton, compare these two')
                    canceled = runtime.chat(sid, 'Never mind')
                    self.assertIn("haven't applied the pending preference update", canceled['assistant']['message'])
                    self.assertEqual(canceled['receipt']['soft'], replacement['receipt']['soft'])
                    self.assertEqual(canceled['products'], replacement['products'])
                    self.assertEqual(canceled['receipt']['pre_reason'], 'comparison_reference_skip')
                    self.assertIsNone(canceled['receipt']['comparison_reference_question'])
                    self.assertNotEqual(canceled['receipt']['suggested_replies'], ['Never mind'])
                    self.assertIsNone(runtime.sessions[sid].pending_comparison_requirements)
                    self.assertFalse(runtime.sessions[sid].pending_compare_reference)

    def test_empty_saved_options_do_not_fall_back_to_displayed_products(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A blue dress')
        reply = runtime.chat(sid, 'How do my saved options differ?')
        self.assertEqual(reply['selection_state']['selected_asins'], [])
        self.assertEqual(reply['products'], first['products'])
        self.assertFalse((reply.get('handoff') or {}).get('selected_products'))

    def test_saved_comparison_wording_does_not_use_displayed_ranks(self):
        for message in ('How do my saved options differ?',
                        "What's the difference between my saved products?",
                        'What are the differences between the items in my shortlist?'):
            with self.subTest(message=message):
                control = parse_control_intent(message)
                self.assertEqual((control.action, control.ranks), ('compare', ()))

    def test_difference_questions_use_existing_comparison_reference_rules(self):
        for message, ranks in (
            ('What is the difference between #1 and #2?', (1, 2)),
            ("What's the difference between #3 and #2?", (3, 2)),
            ('How do the first and second differ?', (1, 2)),
            ('What are the differences between #1, #2, and #3?', (1, 2, 3)),
            ('What is the difference between #1 and #99?', (1, 99)),
        ):
            with self.subTest(message=message):
                control = parse_control_intent(message)
                self.assertEqual((control.action, control.ranks), ('compare', ranks))

    def test_general_or_negated_difference_questions_do_not_select_products(self):
        for message in ('What is the difference between cotton and linen?',
                        "Don't show the difference between #1 and #2",
                        'What is the difference between $10 and $20?'):
            with self.subTest(message=message):
                self.assertIsNone(parse_control_intent(message))


if __name__ == '__main__':
    unittest.main()
