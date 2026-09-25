import unittest

from agentic_workflow import Agent
from mvp.server import _preference_summary
from shopping_agent.retrieval import requirements_from_state


class GiftOccasionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def search(query, top_k):
            self.calls.append(query)
            return [{'product_id': 'TEE', 'score': 1.0}]

        def details(ids):
            return [{'product_id': 'TEE', 'found': True,
                     'title': 'Black cotton T-shirt', 'color': 'black'}]

        self.agent = Agent(search_function=search, details_function=details,
                           trace_enabled=True)
        self.agent.reset('gift', {})

    def test_recipient_occasion_and_target_survive_a_later_product_answer(self):
        first = self.agent.respond('gift',
            'I need a birthday gift for my sister around $40', 1)
        state = self.agent.memory.snapshot('gift')
        self.assertEqual(first['ask_attribute'], 'category')
        self.assertEqual(self.calls, [])
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value,
                         'gift for sister')
        self.assertEqual(state.soft_preferences['shopping_occasion'][0].value,
                         'birthday')
        self.assertEqual(state.soft_preferences['budget_target'][0].value, 40)
        self.assertIn('birthday gift for your sister', first['message'])
        second = self.agent.respond('gift', 'A black T-shirt', 2)
        state = self.agent.memory.snapshot('gift')
        self.assertEqual(state.hard_constraints['category'], 't-shirt')
        self.assertEqual(state.hard_constraints['color'], 'black')
        self.assertEqual(state.soft_preferences['shopping_occasion'][0].value,
                         'birthday')
        self.assertNotIn('birthday', requirements_from_state(state).soft_preferences)
        self.assertNotIn('sister', self.calls[-1])
        self.assertNotIn('birthday', self.calls[-1])
        self.assertIn('birthday gift for your sister', second['message'])

    def test_occasion_correction_and_undo_do_not_change_recipient(self):
        self.agent.respond('gift', 'A birthday gift for my sister', 1)
        self.agent.respond('gift', 'A black T-shirt', 2)
        self.agent.respond('gift', "Actually it's for her graduation", 3)
        state = self.agent.memory.snapshot('gift')
        self.assertEqual(state.soft_preferences['shopping_occasion'][0].value,
                         'graduation')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value,
                         'gift for sister')
        self.agent.respond('gift', 'Undo', 4)
        self.assertEqual(self.agent.memory.snapshot('gift').soft_preferences[
            'shopping_occasion'][0].value, 'birthday')

    def test_occasion_survives_category_switch_and_can_be_withdrawn(self):
        self.agent.respond('gift', 'A birthday gift for my sister', 1)
        self.agent.respond('gift', 'A black T-shirt', 2)
        self.agent.respond('gift', 'Switch to shoes', 3)
        state = self.agent.memory.snapshot('gift')
        self.assertEqual(state.soft_preferences['shopping_occasion'][0].value,
                         'birthday')
        self.agent.respond('gift', 'No occasion, just because', 4)
        self.assertNotIn('shopping_occasion', self.agent.memory.snapshot('gift').soft_preferences)

    def test_recipient_and_occasion_can_both_change_in_one_message(self):
        parsed = self.agent.router.understand_turn(
            'A birthday gift for my sister, actually a graduation gift for my brother')
        updates = {row.slot: row.values for row in parsed.slot_updates
                   if row.operation == 'set'}
        self.assertEqual(updates['shopping_purpose'], ('gift for brother',))
        self.assertEqual(updates['shopping_occasion'], ('graduation',))

    def test_wedding_gift_is_not_a_verified_wedding_wear_requirement(self):
        parsed = self.agent.router.understand_turn(
            'I need a wedding gift for my friend')
        sets = {row.slot: row.values for row in parsed.slot_updates
                if row.operation == 'set'}
        self.assertEqual(sets['shopping_occasion'], ('wedding',))
        self.assertEqual(sets['shopping_purpose'], ('gift for friend',))
        self.assertNotIn('use_case', sets)

    def test_english_recap_treats_occasion_as_context(self):
        summary, _ = _preference_summary({
            'hard': {'category': 't-shirt'},
            'soft': {'shopping_occasion': ['birthday'],
                     'shopping_purpose': ['gift for sister'],
                     'budget_target': [40]},
            'excluded': {},
        })
        self.assertIn('Context: birthday gift for sister.', summary)
        self.assertIn('Prefer: around $40.', summary)
        self.assertNotIn('Prefer: birthday', summary)

    def test_possessive_occasion_keeps_gift_context_out_of_search(self):
        reply = self.agent.respond('gift',
            "I need a black T-shirt for my sister's birthday around $40", 1)
        state = self.agent.memory.snapshot('gift')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value,
                         'gift for sister')
        self.assertEqual(state.soft_preferences['shopping_occasion'][0].value,
                         'birthday')
        self.assertIn('birthday gift for your sister', reply['message'])
        self.assertNotIn('sister', self.calls[-1])
        self.assertNotIn('birthday', self.calls[-1])

    def test_possessive_occasion_correction_replaces_recipient_and_occasion(self):
        self.agent.respond('gift', "A T-shirt for my sister's birthday", 1)
        self.agent.respond('gift', "Actually, for my brother's graduation", 2)
        state = self.agent.memory.snapshot('gift')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value,
                         'gift for brother')
        self.assertEqual(state.soft_preferences['shopping_occasion'][0].value,
                         'graduation')
        self.agent.respond('gift', 'Undo', 3)
        state = self.agent.memory.snapshot('gift')
        self.assertEqual(state.soft_preferences['shopping_purpose'][0].value,
                         'gift for sister')
        self.assertEqual(state.soft_preferences['shopping_occasion'][0].value,
                         'birthday')

    def test_wedding_possessive_does_not_imply_gift(self):
        parsed = self.agent.router.understand_turn(
            "I need a dress for my sister's wedding")
        slots = {row.slot for row in parsed.slot_updates if row.operation == 'set'}
        self.assertNotIn('shopping_purpose', slots)
        self.assertNotIn('shopping_occasion', slots)
        self.assertIn('use_case', slots)


if __name__ == '__main__':
    unittest.main()
