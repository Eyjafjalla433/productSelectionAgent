import unittest

from mvp.audit import verify_audit
from mvp.demo import DEMO_CATALOG
from mvp.server import AgentRuntime


class EmptyPoolRecoveryTests(unittest.TestCase):
    def test_everyday_plural_product_names_are_understood(self):
        router = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive').agent.router
        for phrase, category in (('dresses', 'dress'), ('jackets', 'jacket'),
                                 ('shirts', 'shirt'), ('bags', 'bag'),
                                 ('sneakers', 'shoes'), ('bracelets', 'bracelet')):
            with self.subTest(phrase=phrase):
                parsed = router.understand_turn(f'Show me {phrase}')
                updates = [u.values for u in parsed.slot_updates if u.slot == 'category']
                self.assertIn((category,), updates)

    def test_empty_results_offer_relevant_change_without_relaxing_filters(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a black T-shirt')
        self.assertEqual(first['receipt']['post_reason'], 'empty_eligible_pool')
        self.assertIn('T-shirt search', first['assistant']['message'])
        self.assertIn('revisit the color', first['assistant']['message'])
        self.assertIn('not loosened your requirements', first['assistant']['message'])
        self.assertEqual(first['receipt']['hard']['color'], 'black')

        changed = runtime.chat(sid, 'Actually blue would be better')
        self.assertEqual(changed['receipt']['hard']['color'], 'blue')
        self.assertIn('revisit the color', changed['assistant']['message'])
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['hard']['color'], 'black')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_category_only_empty_pool_does_not_invent_a_filter(self):
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'Show me T-shirts')
        self.assertEqual(result['receipt']['post_reason'], 'empty_eligible_pool')
        self.assertIn('try another product type', result['assistant']['message'])
        self.assertNotIn('revisit the color', result['assistant']['message'])
        switched = runtime.chat(sid, 'Actually, show me dresses instead')
        self.assertEqual(switched['receipt']['hard']['category'], 'dress')
        self.assertTrue(switched['products'])
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['hard']['category'], 't-shirt')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
