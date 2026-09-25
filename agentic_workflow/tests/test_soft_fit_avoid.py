import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from mvp.audit import verify_audit
from mvp.server import AgentRuntime, _preference_summary
from shopping_agent.retrieval import style_matches


class SoftFitAvoidTests(unittest.TestCase):
    def runtime(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': item, 'score': 10 - index}
                    for index, item in enumerate(('BAGGY', 'REGULAR', 'UNKNOWN'))]

        def details(ids):
            titles = {'BAGGY': 'Black baggy T-shirt',
                      'REGULAR': 'Black regular-fit T-shirt',
                      'UNKNOWN': 'Black cotton T-shirt'}
            return [dict(product_id=item, found=True, title=titles[item]) for item in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        return runtime, calls

    def test_degree_language_is_soft_and_undoable(self):
        router = TurnIntentRouter()
        parsed = router.understand_turn("Black T-shirt that isn't too baggy")
        updates = [row for row in parsed.slot_updates if row.slot == 'fit_avoid']
        self.assertEqual([(row.operation, row.values, row.constraint_type)
                          for row in updates], [('set', ('baggy',), 'soft')])
        self.assertFalse(any(row.operation == 'exclude' for row in updates))

        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'Black T-shirt')
        result = runtime.chat(sid, 'Nothing too baggy')
        self.assertEqual(result['receipt']['soft']['fit_avoid'], ['baggy'])
        self.assertNotIn('fit_avoid', result['receipt']['hard'])
        self.assertNotIn('fit_avoid', result['receipt']['excluded'])
        self.assertNotIn('baggy', calls[-1])
        self.assertEqual([row['parent_asin'] for row in result['products']],
                         ['REGULAR', 'UNKNOWN', 'BAGGY'])
        self.assertEqual(result['receipt']['ranking_method'],
                         'search_tool+supported_soft_fit_guidance')
        recap, _ = _preference_summary(result['receipt'])
        self.assertIn('Prefer: nothing too baggy.', recap)
        self.assertIn("haven't ruled them out", result['assistant']['message'])
        explanation = runtime.chat(sid, 'Why is #1 first?')
        self.assertIn('remain available', explanation['assistant']['message'])
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('fit_avoid', undone['receipt']['soft'])
        self.assertEqual([row['parent_asin'] for row in undone['products']],
                         ['BAGGY', 'REGULAR', 'UNKNOWN'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_unlabeled_fit_is_not_claimed_as_good_fit(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'Black T-shirt, not too baggy')
        signals = {row['parent_asin']: next(signal for signal in row['match']['signals']
                   if signal['slot'] == 'fit_avoid') for row in result['products']}
        self.assertEqual(signals['BAGGY']['status'], 'conflict')
        self.assertEqual(signals['UNKNOWN']['status'], 'unknown')
        self.assertTrue(any('preferred to avoid' in row['text']
                            for row in result['products'][-1]['advice']['cons']))
        cleared = runtime.chat(sid, 'Baggy is fine now')
        self.assertNotIn('fit_avoid', cleared['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_no_fit_evidence_does_not_claim_reranking(self):
        self.assertFalse(style_matches({'title': 'Black T-shirt, not too baggy'},
                                       'baggy'))
        def search(query, top_k):
            return [{'product_id': 'A', 'score': 2},
                    {'product_id': 'B', 'score': 1}]
        def details(ids):
            return [{'product_id': item, 'found': True,
                     'title': 'Black cotton T-shirt'} for item in ids]
        runtime = AgentRuntime(Agent(search_function=search,
                                     details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'Black T-shirt, not too baggy')
        self.assertEqual(result['receipt']['ranking_method'], 'search_tool')
        self.assertIn("don't verify whether the fit is too baggy",
                      result['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
