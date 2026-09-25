import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from mvp.audit import verify_audit
from mvp.server import AgentRuntime, _correction_acknowledgement


class TentativePreferenceTests(unittest.TestCase):
    def test_relaxation_and_new_requirement_apply_together_and_undo_together(self):
        records = {'both': 'Breathable black cotton T-shirt',
                   'cotton': 'Black cotton T-shirt',
                   'breathable': 'Breathable black polyester T-shirt'}

        def search(query, top_k):
            return [{'product_id': asin, 'score': 3 - index}
                    for index, asin in enumerate(records)]

        def details(ids):
            return [dict(product_id=asin, found=True, title=records[asin]) for asin in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        initial = runtime.chat(sid, 'Only breathable black T-shirts')
        changed = runtime.chat(sid, 'Breathable is optional, but cotton is a must')
        self.assertEqual(changed['receipt']['hard'],
                         {'category': 't-shirt', 'color': 'black', 'material': 'cotton'})
        self.assertEqual(changed['receipt']['soft']['feature'], ['breathable'])
        self.assertEqual([p['parent_asin'] for p in changed['products']], ['both', 'cotton'])
        self.assertIn('breathable as a preference, not a must-have', changed['assistant']['message'])
        self.assertIn("I'll require cotton", changed['assistant']['message'])
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['hard'], initial['receipt']['hard'])
        self.assertEqual(restored['receipt']['soft'], initial['receipt']['soft'])
        self.assertEqual(restored['products'], initial['products'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_breathability_ranking_uses_consistent_claims_and_keeps_ties_stable(self):
        records = {
            'unknown': {'title': 'Black T-shirt'},
            'contradictory': {'title': 'Breathable black T-shirt',
                              'features': ['Not breathable']},
            'yes1': {'title': 'Breathable black T-shirt'},
            'no': {'title': 'Non-breathable black T-shirt'},
            'yes2': {'title': 'Black T-shirt', 'description': 'Breathable fabric'},
        }

        def search(query, top_k):
            return [{'product_id': asin, 'score': 10 - index}
                    for index, asin in enumerate(records)]

        def details(ids):
            return [dict(product_id=asin, found=True, **records[asin]) for asin in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'Black T-shirt, I prefer breathable')
        self.assertEqual([p['parent_asin'] for p in result['products']],
                         ['yes1', 'yes2', 'unknown', 'contradictory', 'no'])
        firm = runtime.chat(sid, 'Only breathable black T-shirts')
        self.assertEqual([p['parent_asin'] for p in firm['products']], ['yes1', 'yes2'])
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['products'], result['products'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_relaxation_confirmation_requires_matching_saved_state(self):
        receipt = {'state_changes': [{'kind': 'removed', 'group': 'hard',
                                     'slot': 'feature', 'previous': 'breathable'}],
                   'hard': {}, 'soft': {'feature': ['breathable']}}
        self.assertEqual(_correction_acknowledgement('Breathable is optional', receipt),
                         "I'll keep breathable as a preference, not a must-have.")
        for changed in ({'soft': {}}, {'hard': {'feature': 'breathable'}},
                        {'soft': {'feature': ['lightweight']}}, {'requirement_undo': True}):
            self.assertIsNone(_correction_acknowledgement('Breathable is optional',
                                                          dict(receipt, **changed)))

    def test_feature_relaxation_requires_explicit_revision(self):
        router = TurnIntentRouter()
        ordinary = router.understand_turn('I prefer breathable')
        self.assertFalse(any(row.slot == 'feature' and row.operation == 'clear'
                             for row in ordinary.slot_updates))
        revised = router.understand_turn('Breathable is optional, but black is required')
        self.assertTrue(any(row.slot == 'feature' and row.operation == 'clear'
                            for row in revised.slot_updates))
        self.assertFalse(any(row.slot == 'color' and row.operation == 'clear'
                             for row in revised.slot_updates))
        self.assertTrue(any(row.slot == 'color' and row.constraint_type == 'hard'
                            for row in revised.slot_updates))

    def test_optional_pronoun_only_relaxes_an_unambiguous_adjacent_detail(self):
        router = TurnIntentRouter()
        for message, cleared in (
            ("Breathable would be nice, but it's not essential", {'feature'}),
            ('Black is required, breathable would be nice, but it is optional', {'feature'}),
            ('Black and breathable would be nice, but it is optional', set()),
            ('Breathable would be nice, but it is essential', set()),
        ):
            with self.subTest(message=message):
                parsed = router.understand_turn(message)
                actual = {row.slot for row in parsed.slot_updates if row.operation == 'clear'}
                self.assertEqual(actual, cleared)

    def test_explicit_feature_relaxation_restores_options_and_can_be_undone(self):
        for message in ('Breathable is optional', 'Breathable is not essential',
                        'Actually I prefer breathable',
                        'Breathable would be nice, but it is not essential',
                        "Breathable would be nice, but it's optional"):
            with self.subTest(message=message):
                def search(query, top_k):
                    return [{'product_id': 'no', 'score': 2}, {'product_id': 'yes', 'score': 1}]

                def details(ids):
                    return [dict(product_id=asin, found=True,
                                 title=('Non-breathable' if asin == 'no' else 'Breathable') + ' black T-shirt')
                            for asin in ids]

                runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                             trace_enabled=True), orchestration_mode='adaptive')
                sid = runtime.new_session()['session_id']
                firm = runtime.chat(sid, 'Only breathable black T-shirts')
                self.assertEqual([p['parent_asin'] for p in firm['products']], ['yes'])
                relaxed = runtime.chat(sid, message)
                self.assertNotIn('feature', relaxed['receipt']['hard'])
                self.assertEqual(relaxed['receipt']['soft']['feature'], ['breathable'])
                self.assertEqual(relaxed['receipt']['hard']['color'], 'black')
                self.assertEqual(relaxed['receipt']['hard']['category'], firm['receipt']['hard']['category'])
                self.assertEqual({p['parent_asin'] for p in relaxed['products']}, {'no', 'yes'})
                self.assertEqual(relaxed['products'][0]['parent_asin'], 'yes')
                self.assertIn("I'll keep breathable as a preference, not a must-have.",
                              relaxed['assistant']['message'])
                restored = runtime.chat(sid, 'Undo')
                self.assertEqual(restored['receipt']['hard'], firm['receipt']['hard'])
                self.assertEqual(restored['products'], firm['products'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_only_marks_the_named_feature_as_required_without_capturing_not_only(self):
        router = TurnIntentRouter()
        for message, expected in (
            ('Only breathable black T-shirts', 'hard'),
            ('I only want breathable black T-shirts', 'hard'),
            ('I prefer breathable black T-shirts', 'soft'),
            ('I only prefer breathable black T-shirts', 'soft'),
            ('Not only breathable but also black T-shirts', 'soft'),
        ):
            with self.subTest(message=message):
                updates = [row for row in router.understand_turn(message).slot_updates
                           if row.slot == 'feature' and row.operation == 'set']
                self.assertEqual(updates[0].constraint_type, expected)
                if message.startswith('Not only'):
                    self.assertFalse(any(row.slot == 'feature' and row.operation == 'exclude'
                                         for row in router.understand_turn(message).slot_updates))

    def test_only_promotes_feature_to_filter_and_undo_restores_preference(self):
        def search(query, top_k):
            return [{'product_id': 'no', 'score': 2}, {'product_id': 'yes', 'score': 1}]

        def details(ids):
            return [dict(product_id=asin, found=True,
                         title=('Non-breathable' if asin == 'no' else 'Breathable') + ' black T-shirt')
                    for asin in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black T-shirt, I prefer breathable')
        self.assertIn('feature', first['receipt']['soft'])
        self.assertEqual({p['parent_asin'] for p in first['products']}, {'no', 'yes'})
        firm = runtime.chat(sid, 'Only breathable black T-shirts')
        self.assertIn('feature', firm['receipt']['hard'])
        self.assertNotIn('feature', firm['receipt']['soft'])
        self.assertEqual([p['parent_asin'] for p in firm['products']], ['yes'])
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['soft'], first['receipt']['soft'])
        self.assertEqual(restored['products'], first['products'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

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
