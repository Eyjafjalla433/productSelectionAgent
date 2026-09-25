import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.control_intent import parse_control_intent
from mvp.server import AgentRuntime


class ComfortQuestionTests(unittest.TestCase):
    def runtime(self, facet=False):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 20 - i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True,
                         title=('Black breathable cotton tshirt' if facet else 'Black comfortable cotton tshirt')
                         if i == '1' else 'Black cotton tshirt')
                    for i in ids]

        return AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                            orchestration_mode='adaptive'), calls

    def test_comparison_does_not_turn_question_into_preference_or_retrieve(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a black tshirt')
        before = len(calls)
        reply = runtime.chat(sid, 'Which one is most comfortable?')
        self.assertEqual(reply['receipt']['pre_reason'], 'comfort_question')
        self.assertEqual(reply['receipt']['comfort_question']['verified_winner'], None)
        self.assertIn("can't honestly pick", reply['assistant']['message'])
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertEqual(reply['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(reply['receipt']['soft'], first['receipt']['soft'])
        self.assertEqual(len(calls), before)
        self.assertFalse(any(event['stage'].startswith('2_retrieval')
                             for event in runtime.agent.get_trace(sid, reply['turn'])['events']))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_specific_item_and_unknown_rank(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        before = len(calls)
        specific = runtime.chat(sid, 'Is #2 comfortable?')
        self.assertEqual(specific['receipt']['pre_reason'], 'comfort_question')
        self.assertIn("can't verify how comfortable #2 feels", specific['assistant']['message'])
        missing = runtime.chat(sid, 'Is #99 comfortable?')
        self.assertIn("rank isn't in the current list", missing['assistant']['message'])
        self.assertEqual(len(calls), before)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_parser_keeps_actual_shopping_preference(self):
        self.assertEqual(parse_control_intent('Which one is most comfortable?').action,
                         'comfort_question')
        self.assertEqual(parse_control_intent('Is #2 comfortable?').action,
                         'comfort_question')
        self.assertIsNone(parse_control_intent('I want comfortable shoes'))

    def test_offered_breathability_is_used_and_undoable(self):
        runtime, calls = self.runtime(facet=True)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a black tshirt')
        question = runtime.chat(sid, 'Which one is most comfortable?')
        self.assertIn('breathability', question['receipt']['comfort_question']['offered_facets'])
        before = len(calls)
        answer = runtime.chat(sid, 'breathability')
        self.assertGreater(len(calls), before)
        self.assertEqual(answer['receipt']['comfort_followup_answer']['value'], 'breathable')
        self.assertIn('breathable', answer['receipt']['soft']['feature'])
        undone = runtime.chat(sid, 'undo')
        self.assertNotIn('breathable', undone['receipt']['soft'].get('feature', ()))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unoffered_word_does_not_become_comfort_choice(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        question = runtime.chat(sid, 'Which one is most comfortable?')
        self.assertNotIn('offered_facets', question['receipt']['comfort_question'])

    def test_a_new_request_cancels_the_offered_choice(self):
        runtime, _ = self.runtime(facet=True)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Which one is most comfortable?')
        changed = runtime.chat(sid, 'Actually, I want a blue tshirt')
        self.assertNotIn('comfort_followup_answer', changed['receipt'])
        later = runtime.chat(sid, 'breathability')
        self.assertNotIn('comfort_followup_answer', later['receipt'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_choice_and_budget_in_one_reply(self):
        runtime, _ = self.runtime(facet=True)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Which one is most comfortable?')
        reply = runtime.chat(sid, 'breathability, and under $30')
        self.assertEqual(reply['receipt']['comfort_followup_answer']['value'], 'breathable')
        self.assertIn('breathable', reply['receipt']['soft']['feature'])
        self.assertEqual(reply['receipt']['hard'].get('price_max'), 30.0)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_decline_keeps_results_without_search(self):
        for decline in ('No thanks', 'neither', 'none of those', 'show me first'):
            with self.subTest(decline=decline):
                runtime, calls = self.runtime(facet=True)
                sid = runtime.new_session()['session_id']
                first = runtime.chat(sid, 'black tshirt')
                runtime.chat(sid, 'Which one is most comfortable?')
                before = len(calls)
                reply = runtime.chat(sid, decline)
                self.assertEqual(reply['receipt']['pre_reason'], 'comfort_question_skip')
                self.assertEqual(len(calls), before)
                self.assertEqual(reply['receipt']['soft'], first['receipt']['soft'])
                self.assertEqual([p['parent_asin'] for p in reply['products']],
                                 [p['parent_asin'] for p in first['products']])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_decline_and_budget_preserves_only_budget(self):
        runtime, _ = self.runtime(facet=True)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Which one is most comfortable?')
        reply = runtime.chat(sid, 'No thanks, but under $30')
        self.assertEqual(reply['receipt']['hard'].get('price_max'), 30.0)
        self.assertNotIn('breathable', reply['receipt']['soft'].get('feature', ()))
        self.assertEqual(reply['receipt']['comfort_followup_decline']['additional_requirements'],
                         'under $30')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_no_thanks_without_special_offer_does_not_search_again(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        before = len(calls)
        reply = runtime.chat(sid, 'No thanks')
        self.assertEqual(reply['receipt']['pre_reason'], 'offer_declined')
        self.assertEqual(len(calls), before)
        self.assertEqual(reply['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
