import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.demo import DEMO_CATALOG
from mvp.server import AgentRuntime


class SingleTurnRevisionTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def search(query, top_k):
            self.calls.append(query)
            return [{'product_id': 'BLUE', 'score': 1.0}]

        def details(ids):
            return [{'product_id': 'BLUE', 'found': True,
                     'title': 'Blue cotton T-shirt', 'color': 'blue'}]

        self.agent = Agent(search_function=search, details_function=details,
                           trace_enabled=True)
        self.agent.reset('revision', {})

    def test_explicit_same_message_color_correction_replaces_old_color(self):
        result = self.agent.respond('revision', 'I need a black T-shirt, actually blue', 1)
        state = self.agent.memory.snapshot('revision')
        self.assertEqual(state.hard_constraints['category'], 't-shirt')
        self.assertEqual(state.hard_constraints['color'], 'blue')
        self.assertTrue(result['recommendations'])
        self.assertNotIn('black', self.calls[-1])

    def test_tentative_same_message_correction_stays_soft(self):
        parsed = self.agent.router.understand_turn('I need a black T-shirt, maybe blue instead')
        color = [u for u in parsed.slot_updates if u.slot == 'color' and u.operation == 'set']
        self.assertEqual([(u.values, u.constraint_type) for u in color], [(('blue',), 'soft')])

    def test_unrelated_late_detail_preserves_earlier_color(self):
        parsed = self.agent.router.understand_turn('I need a black T-shirt, actually size M')
        sets = {u.slot: u.values for u in parsed.slot_updates if u.operation == 'set'}
        self.assertEqual(sets['color'], ('black',))
        self.assertEqual(sets['size'], ('m',))

    def test_ordinary_alternatives_remain_alternatives(self):
        parsed = self.agent.router.understand_turn('I need a black or blue T-shirt')
        colors = [u.values for u in parsed.slot_updates
                  if u.slot == 'color' and u.operation == 'set']
        self.assertEqual(colors, [('black', 'blue')])

    def test_instead_of_keeps_desired_value_not_rejected_value(self):
        for message in ('I need a blue T-shirt instead of black',
                        'I need a black T-shirt, blue instead of black',
                        'I need a black T-shirt, actually blue instead of black'):
            with self.subTest(message=message):
                parsed = self.agent.router.understand_turn(message)
                colors = [u.values for u in parsed.slot_updates
                          if u.slot == 'color' and u.operation == 'set']
                self.assertEqual(colors, [('blue',)])

    def test_material_category_and_price_can_be_corrected_in_one_message(self):
        examples = [
            ('I need a cotton T-shirt, actually linen', 'material', ('linen',)),
            ('I need a dress, actually a jacket', 'category', ('jacket',)),
            ('I need a T-shirt under $50, actually under $40', 'price_max', (40.0,)),
        ]
        for message, slot, expected in examples:
            with self.subTest(message=message):
                parsed = self.agent.router.understand_turn(message)
                updates = [u.values for u in parsed.slot_updates
                           if u.slot == slot and u.operation == 'set']
                self.assertEqual(updates, [expected])

    def test_two_corrections_keep_independent_original_details(self):
        parsed = self.agent.router.understand_turn(
            'I need a black cotton T-shirt under $50, actually blue and under $40')
        sets = {u.slot: u.values for u in parsed.slot_updates if u.operation == 'set'}
        self.assertEqual(sets['category'], ('t-shirt',))
        self.assertEqual(sets['material'], ('cotton',))
        self.assertEqual(sets['color'], ('blue',))
        self.assertEqual(sets['price_max'], (40.0,))

    def test_runtime_only_shows_corrected_color_and_audits_it(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a red dress, actually blue, under $50')
        self.assertEqual(result['receipt']['hard']['color'], 'blue')
        self.assertTrue(result['products'])
        self.assertTrue(all('blue' in product['title'].lower()
                            or 'navy' in product['title'].lower()
                            for product in result['products']))
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_gift_recipient_correction_replaces_person_in_same_message(self):
        for message, expected in (
            ('A gift for my dad, actually for my mom', 'gift for mom'),
            ('A gift for my sister for her birthday, around $40', 'gift for sister'),
        ):
            with self.subTest(message=message):
                parsed = self.agent.router.understand_turn(message)
                purposes = [u.values for u in parsed.slot_updates
                            if u.slot == 'shopping_purpose' and u.operation == 'set']
                self.assertEqual(purposes, [(expected,)])


if __name__ == '__main__':
    unittest.main()
