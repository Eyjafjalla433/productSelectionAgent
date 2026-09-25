import unittest

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit
from mvp.product_question import answer_product_question
from mvp.control_intent import parse_detail_reference, parse_detail_followup, is_uncertain_reply


class ResultCountLanguageTests(unittest.TestCase):
    def test_natural_item_correction_reuses_the_answered_topic(self):
        for reply in ('Sorry, I meant the second one', 'I meant #2',
                      'Actually, the second one', 'No, #2', 'Sorry I mean #2 please'):
            with self.subTest(reply=reply):
                calls = []
                runtime = self.runtime(2, search_calls=calls)
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'A black T-shirt')
                runtime.chat(sid, 'How do I wash #1?')
                search_count = len(calls)
                corrected = runtime.chat(sid, reply)
                self.assertEqual(corrected['receipt']['pre_reason'], 'product_detail')
                self.assertIn('#2:', corrected['assistant']['message'])
                self.assertIn('Machine wash cold', corrected['assistant']['message'])
                self.assertEqual(corrected['products'], first['products'])
                self.assertEqual(corrected['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(corrected['selection_state']['selected_asins'], [])
                self.assertEqual(len(calls), search_count)
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_item_correction_needs_a_topic_and_a_single_unambiguous_reference(self):
        self.assertIsNone(parse_detail_followup('Sorry, I meant #2', None))
        for message in ('I meant blue', 'Not the second one', 'No, not #2',
                        'Actually, #2 and #3', 'I meant #2, change to blue',
                        'Sorry, select #2', 'I meant #2 instead of #1'):
            with self.subTest(message=message):
                self.assertIsNone(parse_detail_followup(message, 'care'))

    def test_invalid_item_correction_keeps_topic_for_a_valid_repair(self):
        runtime = self.runtime(2)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A black T-shirt')
        runtime.chat(sid, 'How do I wash #1?')
        invalid = runtime.chat(sid, 'Sorry, I meant #99')
        self.assertEqual(invalid['receipt']['detail_question']['attribute'], 'care')
        self.assertEqual(invalid['products'], first['products'])
        self.assertNotIn('Machine wash cold', invalid['assistant']['message'])
        fixed = runtime.chat(sid, 'Actually, #2')
        self.assertIn('#2:', fixed['assistant']['message'])
        self.assertIn('Machine wash cold', fixed['assistant']['message'])
        self.assertIsNone(fixed['receipt']['detail_question'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_uncertainty_detection_requires_a_standalone_reply(self):
        self.assertTrue(is_uncertain_reply('  Let   me think.  '))
        for message in ("I'm not sure, change to blue", 'Maybe later, compare #1 and #2',
                        'I do not know if cotton is better', 'I have decided', '',
                        "I'm not sure about #2's care instructions"):
            with self.subTest(message=message):
                self.assertFalse(is_uncertain_reply(message))

    def test_uncertain_detail_reply_preserves_topic_without_searching_or_selecting(self):
        for reply in ("I'm not sure yet", 'Not sure', 'I don’t know yet',
                      'Let me think', "I haven't decided yet", 'Maybe later'):
            with self.subTest(reply=reply):
                calls = []
                runtime = self.runtime(2, search_calls=calls)
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'A black T-shirt')
                runtime.chat(sid, 'How should I wash it?')
                search_count = len(calls)
                paused = runtime.chat(sid, reply)
                self.assertEqual(paused['receipt']['detail_question']['attribute'], 'care')
                self.assertEqual(paused['receipt']['suggested_replies'], ['#1', '#2', 'Never mind'])
                self.assertEqual(paused['products'], first['products'])
                self.assertEqual(paused['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(paused['receipt']['soft'], first['receipt']['soft'])
                self.assertEqual(paused['selection_state']['selected_asins'], [])
                self.assertIn('No rush', paused['assistant']['message'])
                self.assertEqual(len(calls), search_count)
                resumed = runtime.chat(sid, 'the second one')
                self.assertIn('Machine wash cold', resumed['assistant']['message'])
                self.assertIn('#2:', resumed['assistant']['message'])
                self.assertIsNone(resumed['receipt']['detail_question'])
                self.assertEqual(len(calls), search_count)
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_repeated_uncertainty_can_be_dismissed_without_changing_preferences(self):
        runtime = self.runtime(2)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A black T-shirt')
        runtime.chat(sid, 'Tell me more about it')
        for reply in ('Not sure', 'Let me think', 'Maybe later'):
            paused = runtime.chat(sid, reply)
            self.assertIsNotNone(paused['receipt']['detail_question'])
            self.assertIn('Never mind', paused['receipt']['suggested_replies'])
        dismissed = runtime.chat(sid, 'Never mind')
        self.assertIsNone(dismissed['receipt']['detail_question'])
        self.assertEqual(dismissed['products'], first['products'])
        self.assertEqual(dismissed['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_uncertainty_with_new_requirements_is_not_swallowed_as_a_pause(self):
        runtime = self.runtime(2)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'A black T-shirt')
        runtime.chat(sid, 'Tell me more about it')
        changed = runtime.chat(sid, "I'm not sure yet, change to blue")
        self.assertIsNone(runtime.sessions[sid].pending_detail_attribute)
        self.assertIn('blue', str(changed['receipt']['hard']))
        self.assertNotIn('No rush', changed['assistant']['message'])

    def test_clarification_dismissal_keeps_saved_products_and_has_a_visible_exit(self):
        runtime = self.runtime(5)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A black T-shirt')
        saved = runtime.chat(sid, 'Select #1')
        question = runtime.chat(sid, 'Tell me more about it')
        # Selecting an item is not a detail-question focus: ask before guessing.
        self.assertEqual(question['receipt']['suggested_replies'], ['#1', '#2', '#3', 'Never mind'])
        dismissed = runtime.chat(sid, question['receipt']['suggested_replies'][-1])
        self.assertEqual(dismissed['selection_state']['selected_asins'], saved['selection_state']['selected_asins'])
        self.assertTrue(dismissed['selection_state']['selected_asins'])
        self.assertEqual(dismissed['products'], first['products'])
        self.assertIsNone(dismissed['receipt']['detail_question'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_canceling_detail_clarification_preserves_search_and_clears_reply_buttons(self):
        for reply in ('Never mind', 'Skip that question', 'Cancel'):
            with self.subTest(reply=reply):
                runtime = self.runtime(2)
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'A black T-shirt')
                runtime.chat(sid, 'Tell me more about it')
                dismissed = runtime.chat(sid, reply)
                self.assertIn('leave that question', dismissed['assistant']['message'])
                self.assertEqual(dismissed['products'], first['products'])
                self.assertEqual(dismissed['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(dismissed['receipt']['soft'], first['receipt']['soft'])
                self.assertIsNone(dismissed['receipt']['detail_question'])
                self.assertIsNone(dismissed['receipt']['suggested_replies'])
                self.assertIsNone(runtime.sessions[sid].pending_detail_attribute)
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_overview_omits_placeholders_and_marks_truncated_source_text(self):
        answer = answer_product_question({
            'title': 'Black T-shirt', 'price': True,
            'details': {'Material': 'Unknown', 'Care': 'N/A', 'Weight': float('nan'),
                        'Available': False, 'Fit': 'Not provided'},
            'features': ['null', 'Ribbed crew neck', 'Ribbed crew neck'],
            'description': 'Long source description ' * 30,
        }, 1, 'overview', 'en')
        self.assertEqual(answer.count('Ribbed crew neck'), 1)
        self.assertIn('...', answer)
        self.assertNotIn('Catalog price:', answer)
        for placeholder in ('Unknown', 'N/A', 'nan', 'False', 'Not provided', 'null'):
            self.assertNotIn(placeholder, answer)
        self.assertTrue(all(len(line) <= 242 for line in answer.splitlines() if line.startswith('- ')))

    def test_overview_with_only_placeholder_fields_has_one_missing_details_note(self):
        answer = answer_product_question({'title': 'T-shirt', 'details': {'Material': 'unknown'},
                                          'features': ['N/A'], 'description': 'not specified'},
                                         1, 'overview', 'en')
        self.assertEqual(answer, '#1: T-shirt\nNo additional detail fields are available in this listing.')

    def test_preference_recap_preserves_pending_product_reference(self):
        runtime = self.runtime(2)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A black T-shirt')
        runtime.chat(sid, 'Tell me more about it')
        recap = runtime.chat(sid, 'What are my preferences?')
        self.assertEqual(runtime.sessions[sid].pending_detail_attribute, 'overview')
        self.assertEqual(recap['receipt']['suggested_replies'], ['#1', '#2', 'Never mind'])
        self.assertIn('which product', recap['assistant']['message'])
        self.assertEqual(recap['products'], first['products'])
        answer = runtime.chat(sid, 'the second one')
        self.assertIn('#2:', answer['assistant']['message'])
        self.assertIn('Care: Machine wash cold', answer['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_new_search_discards_pending_product_reference(self):
        runtime = self.runtime(2)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'A black T-shirt')
        runtime.chat(sid, 'Tell me more about it')
        runtime.chat(sid, 'Change to blue')
        self.assertIsNone(runtime.sessions[sid].pending_detail_attribute)

    def test_pending_detail_accepts_a_natural_ordinal_without_selecting(self):
        for reply in ('the second one', 'second', 'The second product, please', '#2 please'):
            with self.subTest(reply=reply):
                runtime = self.runtime(2)
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'A black T-shirt')
                runtime.chat(sid, 'Tell me more about it')
                result = runtime.chat(sid, reply)
                self.assertIn('#2:', result['assistant']['message'])
                self.assertIn('Care: Machine wash cold', result['assistant']['message'])
                self.assertEqual(result['products'], first['products'])
                self.assertEqual(result['receipt']['hard'], first['receipt']['hard'])
                self.assertEqual(result['receipt']['pre_reason'], 'product_detail')
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_detail_reference_repair_is_scoped_and_does_not_swallow_other_intents(self):
        self.assertIsNone(parse_detail_reference('the second one', None))
        for message in ('not the second one', 'select the second one', 'second and third',
                        'the second one but blue', 'maybe the second one'):
            with self.subTest(message=message):
                self.assertIsNone(parse_detail_reference(message, 'overview'))

    def test_unavailable_ordinal_keeps_the_detail_question_recoverable(self):
        runtime = self.runtime(2)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A black T-shirt')
        runtime.chat(sid, 'Tell me more about it')
        unavailable = runtime.chat(sid, 'the third one')
        self.assertIn('not in the current results', unavailable['assistant']['message'])
        self.assertEqual(unavailable['products'], first['products'])
        repaired = runtime.chat(sid, 'the second one, please')
        self.assertIn('#2:', repaired['assistant']['message'])
        self.assertIn('Care: Machine wash cold', repaired['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_single_item_reference_binds_before_a_combined_search_change(self):
        records = {'black': {'title': 'Black cotton T-shirt', 'details': {'Care': 'Hand wash'}},
                   'blue': {'title': 'Blue cotton T-shirt', 'details': {'Care': 'Machine wash'}}}

        def search(query, top_k):
            return [{'product_id': asin, 'score': 2 - index} for index, asin in enumerate(records)]

        def details(ids):
            return [dict(product_id=asin, found=True, **records[asin]) for asin in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A black T-shirt')
        changed = runtime.chat(sid, 'Tell me more about it, but change to blue')
        self.assertIn('Care: Hand wash', changed['assistant']['message'])
        self.assertIn('previous list', changed['assistant']['message'])
        self.assertEqual(changed['receipt']['compound_request']['detail_context']['parent_asin'], 'black')
        self.assertEqual([p['parent_asin'] for p in changed['products']], ['blue'])
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['products'], first['products'])
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_multiple_detail_questions_can_use_the_single_visible_item(self):
        runtime = self.runtime(1)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A black T-shirt')
        result = runtime.chat(sid, 'Tell me more about it. What about its material?')
        self.assertIn('Care: Machine wash cold', result['assistant']['message'])
        self.assertNotIn('could not identify', result['assistant']['message'])
        self.assertEqual(result['products'], first['products'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_overview_uses_description_and_does_not_invent_price(self):
        for price in (None, 'unknown', float('nan'), -1):
            with self.subTest(price=price):
                answer = answer_product_question({'title': 'T-shirt', 'price': price,
                                                  'description': ['Ribbed crew neck']},
                                                 1, 'overview', 'en')
                self.assertIn('Ribbed crew neck', answer)
                self.assertNotIn('Catalog price:', answer)
                self.assertNotIn('No additional', answer)

    def runtime(self, count, search_calls=None):
        def search(query, top_k):
            if search_calls is not None:
                search_calls.append((query, top_k))
            return [{'product_id': str(i), 'score': count - i} for i in range(count)]

        def details(ids):
            return [dict(product_id=asin, found=True, title='Black cotton T-shirt',
                         details={'Care': 'Machine wash cold'}) for asin in ids]

        return AgentRuntime(Agent(search_function=search, details_function=details,
                                  trace_enabled=True), orchestration_mode='adaptive')

    def test_single_item_detail_followup_preserves_results_and_requirements(self):
        for question in ('Tell me more about it', 'Check its details', 'What about its material?'):
            with self.subTest(question=question):
                runtime = self.runtime(1)
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'A black T-shirt')
                detail = runtime.chat(sid, question)
                self.assertEqual(detail['products'], first['products'])
                self.assertEqual(detail['receipt']['hard'], first['receipt']['hard'])
                self.assertIn('#1:', detail['assistant']['message'])
                self.assertNotIn('Which product', detail['assistant']['message'])
                if question != 'What about its material?':
                    self.assertIn('Care: Machine wash cold', detail['assistant']['message'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_overview_with_multiple_items_asks_then_accepts_a_bare_rank(self):
        runtime = self.runtime(2)
        sid = runtime.new_session()['session_id']
        initial = runtime.chat(sid, 'A black T-shirt')
        ambiguous = runtime.chat(sid, 'Tell me more about it')
        self.assertIn('Which product', ambiguous['assistant']['message'])
        self.assertEqual(ambiguous['receipt']['suggested_replies'], ['#1', '#2', 'Never mind'])
        self.assertEqual(ambiguous['receipt']['detail_question']['attribute'], 'overview')
        answered = runtime.chat(sid, '#2')
        self.assertIn('#2:', answered['assistant']['message'])
        self.assertIn('Care: Machine wash cold', answered['assistant']['message'])
        self.assertEqual(answered['products'], initial['products'])
        self.assertIsNone(answered['receipt']['detail_question'])
        self.assertEqual(answered['selection_state']['selected_asins'], [])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_single_result_offers_inspection_not_a_forced_comparison(self):
        runtime = self.runtime(1)
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'A black T-shirt')
        message = result['assistant']['message']
        self.assertIn('one option', message)
        self.assertNotIn('1 options', message)
        self.assertNotIn('compare their', message)
        self.assertIn('details', message)
        more = runtime.chat(sid, 'Show me more')
        self.assertEqual(more['products'], result['products'])
        self.assertIn('earlier match', more['assistant']['message'])
        self.assertNotIn('compare them', more['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_multiple_results_still_offer_comparison(self):
        runtime = self.runtime(2)
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'A black T-shirt')
        self.assertIn('2 options', result['assistant']['message'])
        self.assertIn('compare their details', result['assistant']['message'])


if __name__ == '__main__':
    unittest.main()
