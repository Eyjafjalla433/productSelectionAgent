import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from mvp.audit import verify_audit
from mvp.server import AgentRuntime


class RelativeFitTests(unittest.TestCase):
    def runtime(self):
        calls = []
        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 50-i} for i in range(25)]
        def details(ids):
            return [dict(product_id=i, found=True,
                         title='Black fitted cotton tshirt' if int(i) < 6 else 'Black relaxed-fit cotton tshirt')
                    for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True),
                               orchestration_mode='adaptive')
        return runtime, calls

    def test_relative_fit_parses_as_soft_preference_with_extra_slots(self):
        router = TurnIntentRouter()
        for message, expected in (('Something looser, please', 'loose fit'),
                                  ('Less fitted', 'loose fit'),
                                  ('Something more fitted', 'slim fit')):
            with self.subTest(message=message):
                updates = router.understand_turn(message).slot_updates
                self.assertTrue(any(u.slot == 'style' and u.operation == 'set'
                                    and u.values == (expected,) and u.constraint_type == 'soft' for u in updates))
        updates = router.understand_turn('Something looser, size M').slot_updates
        self.assertTrue(any(u.slot == 'style' and u.values == ('loose fit',) for u in updates))
        self.assertTrue(any(u.slot == 'size' and u.values == ('m',) for u in updates))
        self.assertFalse(any(u.slot == 'style' for u in router.understand_turn('Not looser').slot_updates))

    def test_refinement_promotes_supported_fit_and_undo_restores_old_list(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        self.assertTrue(first['products'])
        refined = runtime.chat(sid, 'Something looser, please')
        self.assertEqual(refined['receipt']['soft']['style'], ['loose fit'])
        self.assertEqual(refined['receipt']['hard']['color'], 'black')
        self.assertTrue(refined['products'])
        self.assertIn('relaxed', refined['products'][0]['title'].lower())
        self.assertIn('loose fit', calls[-1])
        self.assertIn(first['products'][6]['parent_asin'], [p['parent_asin'] for p in refined['products']])
        restored = runtime.chat(sid, 'undo')
        self.assertNotIn('style', restored['receipt']['soft'])
        self.assertEqual([p['parent_asin'] for p in restored['products']], [p['parent_asin'] for p in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_reversal_can_demote_hard_fit(self):
        router = TurnIntentRouter()
        updates = router.understand_turn('Actually, something looser').slot_updates
        self.assertTrue(any(u.slot == 'style' and u.operation == 'clear' for u in updates))
        self.assertTrue(any(u.slot == 'style' and u.operation == 'set'
                            and u.values == ('loose fit',) and u.constraint_type == 'soft' for u in updates))

    def test_hard_fit_conflict_asks_once_and_can_be_replaced(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt, must be slim fit')
        proposal = runtime.chat(sid, 'Something looser')
        self.assertEqual(proposal['receipt']['pre_reason'], 'confirm_requirement_change')
        self.assertEqual(proposal['receipt']['hard']['style'], 'slim fit')
        accepted = runtime.chat(sid, 'replace')
        self.assertEqual(accepted['receipt']['hard']['style'], 'loose fit')
        self.assertIn('relaxed', accepted['products'][0]['title'].lower())
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard']['style'], 'slim fit')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_rank_explanation_names_preference_reranking(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        refined = runtime.chat(sid, 'Something looser')
        self.assertEqual(refined['receipt']['ranking_method'], 'search_tool+supported_soft_style_first')
        explanation = runtime.chat(sid, 'Why is #1 first?')
        self.assertIn('Items with catalog evidence for your fit preference come first',
                      explanation['assistant']['message'])
        self.assertEqual(explanation['receipt']['rank_explanation']['ranking_method'],
                         'search_tool+supported_soft_style_first')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_show_more_still_prefers_unseen_after_fit_refinement(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        refined = runtime.chat(sid, 'Something looser')
        more = runtime.chat(sid, 'Show me more')
        self.assertTrue(more['products'])
        self.assertTrue({p['parent_asin'] for p in more['products']}.isdisjoint(
            {p['parent_asin'] for p in refined['products']}))
        self.assertEqual(more['receipt']['soft']['style'], ['loose fit'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
