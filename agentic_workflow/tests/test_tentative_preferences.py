import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from mvp.audit import verify_audit
from mvp.server import AgentRuntime


class TentativePreferenceTests(unittest.TestCase):
    def test_uncertainty_scopes_to_mentioned_detail(self):
        router = TurnIntentRouter()
        cases = (
            ('I want a black T-shirt, not sure about size M',
             {'category': 'hard', 'color': 'hard', 'size': 'soft'}),
            ('Black is a must, but I think size M',
             {'color': 'hard', 'size': 'soft'}),
            ('I might need size M', {'size': 'soft'}),
            ('I am not sure whether I want black or blue', {'color': 'soft'}),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                parsed = router.understand_turn(message)
                actual = {row.slot: row.constraint_type for row in parsed.slot_updates
                          if row.operation == 'set'}
                self.assertEqual(actual, expected)

    def test_natural_size_corrections_and_alternatives(self):
        router = TurnIntentRouter()
        for message, expected in (
                ('Actually I wear L, not M', ('l',)),
                ('I wear L, not XL', ('l',)),
                ('I wear XL, not L', ('xl',)),
                ('I want size M or L', ('m', 'l')),
                ('I want size M or XL', ('m', 'xl'))):
            with self.subTest(message=message):
                values = [row.values for row in router.understand_turn(message).slot_updates
                          if row.slot == 'size' and row.operation == 'set']
                self.assertEqual(values, [expected])
        self.assertFalse(any(row.slot == 'size' for row in
                             router.understand_turn('Not size XL').slot_updates))

    def test_tentative_size_does_not_filter_and_correction_is_undoable(self):
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': 'L', 'score': 2},
                    {'product_id': 'M', 'score': 1}]

        def details(ids):
            return [{'product_id': item, 'found': True,
                     'title': f'Black cotton T-shirt size {item}'} for item in ids]

        runtime = AgentRuntime(Agent(search_function=search,
                                     details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        tentative = runtime.chat(sid,
                                 'I want a black T-shirt, but I think size M')
        self.assertEqual(tentative['receipt']['hard']['color'], 'black')
        self.assertNotIn('size', tentative['receipt']['hard'])
        self.assertEqual(tentative['receipt']['soft']['size'], ['m'])
        self.assertNotIn('size', calls[-1].lower())
        self.assertEqual({row['parent_asin'] for row in tentative['products']},
                         {'M', 'L'})
        self.assertEqual(tentative['products'][0]['parent_asin'], 'M')
        self.assertEqual(tentative['receipt']['ranking_method'],
                         'search_tool+supported_soft_size_first')
        self.assertIn('size M as a preference, not a requirement',
                      tentative['assistant']['message'])

        firm = runtime.chat(sid, 'Actually I wear L, not M')
        self.assertEqual(firm['receipt']['hard']['size'], 'l')
        self.assertNotIn('size', firm['receipt']['soft'])
        self.assertEqual([row['parent_asin'] for row in firm['products']], ['L'])
        restored = runtime.chat(sid, 'Undo')
        self.assertNotIn('size', restored['receipt']['hard'])
        self.assertEqual(restored['receipt']['soft']['size'], ['m'])
        self.assertEqual({row['parent_asin'] for row in restored['products']},
                         {'M', 'L'})
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
