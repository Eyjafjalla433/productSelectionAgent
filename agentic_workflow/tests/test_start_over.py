import unittest

from agentic_workflow import Agent
from intent_router.turn_router import TurnIntentRouter
from mvp.audit import verify_audit
from mvp.server import AgentRuntime


class StartOverTests(unittest.TestCase):
    @staticmethod
    def runtime():
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 20 - i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True, price=20,
                         title='Black cotton tshirt' if int(i) < 6 else 'Blue cotton tshirt')
                    for i in ids]

        runtime = AgentRuntime(Agent(search_function=search, details_function=details,
                                     trace_enabled=True), orchestration_mode='adaptive')
        return runtime, calls

    def test_parser_marks_reset_as_one_clear_operation(self):
        for message in ("Let's start over", 'Start a new search', 'Clear my search requirements',
                        'Reset search only', 'Reset everything'):
            with self.subTest(message=message):
                parsed = TurnIntentRouter().understand_turn(message)
                self.assertEqual([(u.slot, u.operation) for u in parsed.slot_updates],
                                 [('all_requirements', 'clear')])
                self.assertTrue(parsed.decision_evidence['search_reset'])

    def test_reset_is_readable_and_undo_restores_previous_results(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt around $30')
        before = len(calls)
        cleared = runtime.chat(sid, "Let's start over")
        self.assertEqual(cleared['receipt']['hard'], {})
        self.assertEqual(cleared['receipt']['soft'], {})
        self.assertEqual(cleared['receipt']['excluded'], {})
        self.assertEqual(cleared['products'], [])
        self.assertEqual(len(calls), before)
        self.assertIn('cleared', cleared['assistant']['message'])
        self.assertTrue(cleared['receipt']['can_undo_requirements'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(restored['receipt']['soft'], first['receipt']['soft'])
        self.assertEqual([p['parent_asin'] for p in restored['products']],
                         [p['parent_asin'] for p in first['products']])
        redone = runtime.chat(sid, 'redo')
        self.assertEqual(redone['receipt']['hard'], {})
        self.assertEqual(redone['receipt']['soft'], {})
        self.assertEqual(redone['products'], [])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_saved_shortlist_is_not_discarded_implicitly(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        before = len(calls)
        reply = runtime.chat(sid, 'Start over')
        self.assertEqual(reply['receipt']['pre_reason'], 'reset_scope_clarification')
        self.assertEqual(reply['receipt']['question']['options'],
                         ['Reset search only', 'Reset everything', 'Cancel'])
        self.assertEqual(reply['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(reply['selection_state']['selected_asins'], [first['products'][0]['parent_asin']])
        self.assertEqual(len(calls), before)
        scoped = runtime.chat(sid, 'Reset search only')
        self.assertEqual(scoped['receipt']['hard'], {})
        self.assertEqual(scoped['receipt']['search_reset_scope'], 'search_only')
        self.assertEqual(scoped['selection_state']['selected_asins'], [first['products'][0]['parent_asin']])
        self.assertEqual(len(calls), before)
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(restored['selection_state']['selected_asins'], [first['products'][0]['parent_asin']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_category_switch_undo_restores_shortlist_and_hidden_products(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        first_asins = [p['parent_asin'] for p in first['products']]
        runtime.chat(sid, 'Select #1')
        runtime.chat(sid, 'Reject #2')
        changed = runtime.chat(sid, 'I need blue shoes instead')
        self.assertEqual(changed['receipt']['hard']['category'], 'shoes')
        self.assertEqual(changed['selection_state']['selected_asins'], [])
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual([p['parent_asin'] for p in restored['products']], first_asins)
        self.assertEqual(restored['selection_state']['selected_asins'], [first_asins[0]])
        self.assertIn(first_asins[1], restored['receipt']['rejected_asins'])
        self.assertIn('brought back', restored['assistant']['message'])
        redone = runtime.chat(sid, 'Redo')
        self.assertEqual(redone['receipt']['hard']['category'], 'shoes')
        self.assertEqual(redone['selection_state']['selected_asins'], [])
        self.assertEqual(redone['receipt']['rejected_asins'], [])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_new_direction_after_reset_can_be_undone_stepwise(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt')
        runtime.chat(sid, 'start over')
        new = runtime.chat(sid, 'blue tshirt')
        self.assertEqual(new['receipt']['hard']['color'], 'blue')
        self.assertNotIn('material', new['receipt']['hard'])
        blank = runtime.chat(sid, 'undo')
        self.assertEqual(blank['receipt']['hard'], {})
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_hidden_product_is_not_discarded_implicitly(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        rejected = runtime.chat(sid, 'Reject #1')
        self.assertIn(first['products'][0]['parent_asin'], rejected['receipt']['rejected_asins'])
        before = len(calls)
        guarded = runtime.chat(sid, 'Start over')
        self.assertEqual(guarded['receipt']['pre_reason'], 'reset_scope_clarification')
        self.assertEqual(guarded['receipt']['rejected_asins'], rejected['receipt']['rejected_asins'])
        self.assertEqual(len(calls), before)
        scoped = runtime.chat(sid, 'Reset search only')
        self.assertEqual(scoped['receipt']['hard'], {})
        self.assertEqual(list(scoped['receipt']['rejected_asins']), rejected['receipt']['rejected_asins'])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(list(restored['receipt']['rejected_asins']), rejected['receipt']['rejected_asins'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_scope_choice_can_be_cancelled_without_search(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        runtime.chat(sid, 'Start over')
        before = len(calls)
        cancelled = runtime.chat(sid, 'Cancel')
        self.assertEqual(cancelled['receipt']['pre_reason'], 'reset_scope_cancelled')
        self.assertEqual(cancelled['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(cancelled['selection_state']['selected_asins'], [first['products'][0]['parent_asin']])
        self.assertEqual(len(calls), before)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_full_reset_restores_shortlist_and_hidden_item_on_undo(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        selected_id = first['products'][0]['parent_asin']
        hidden_id = first['products'][1]['parent_asin']
        runtime.chat(sid, 'Select #1')
        runtime.chat(sid, 'Reject #2')
        runtime.chat(sid, 'Start over')
        before = len(calls)
        cleared = runtime.chat(sid, 'Reset everything')
        self.assertEqual(cleared['receipt']['search_reset_scope'], 'everything')
        self.assertEqual(cleared['receipt']['hard'], {})
        self.assertEqual(cleared['products'], [])
        self.assertEqual(cleared['selection_state']['selected_asins'], [])
        self.assertEqual(list(cleared['receipt']['rejected_asins']), [])
        self.assertEqual(len(calls), before)
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(restored['selection_state']['selected_asins'], [selected_id])
        self.assertIn(hidden_id, restored['receipt']['rejected_asins'])
        self.assertEqual([p['parent_asin'] for p in restored['products']],
                         [p['parent_asin'] for p in first['products']])
        redone = runtime.chat(sid, 'redo')
        self.assertEqual(redone['receipt']['hard'], {})
        self.assertEqual(redone['selection_state']['selected_asins'], [])
        self.assertEqual(list(redone['receipt']['rejected_asins']), [])
        self.assertEqual(redone['products'], [])
        restored_again = runtime.chat(sid, 'undo')
        self.assertIn(hidden_id, restored_again['receipt']['rejected_asins'])
        more = runtime.chat(sid, 'Show me more')
        self.assertNotIn(hidden_id, [p['parent_asin'] for p in more['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_intervening_requirement_undo_does_not_restore_full_reset_early(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        runtime.chat(sid, 'Reset everything')
        runtime.chat(sid, 'blue tshirt')
        undone_blue = runtime.chat(sid, 'undo')
        self.assertEqual(undone_blue['receipt']['hard'], {})
        self.assertEqual(undone_blue['selection_state']['selected_asins'], [])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(restored['selection_state']['selected_asins'],
                         [first['products'][0]['parent_asin']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_repeated_full_resets_restore_their_own_snapshot(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        runtime.chat(sid, 'Reset everything')
        runtime.chat(sid, 'undo')
        runtime.chat(sid, 'Reset everything')
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(restored['selection_state']['selected_asins'],
                         [first['products'][0]['parent_asin']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_abandoned_redo_releases_full_reset_snapshot(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        runtime.chat(sid, 'Reset everything')
        self.assertTrue(runtime.sessions[sid].reset_snapshots)
        runtime.chat(sid, 'undo')
        self.assertTrue(runtime.sessions[sid].reset_snapshots)
        runtime.chat(sid, 'blue tshirt')
        self.assertFalse(runtime.sessions[sid].reset_snapshots)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_full_reset_is_undoable_with_only_a_shortlist(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        runtime.chat(sid, 'start over')
        scoped = runtime.chat(sid, 'Reset search only')
        self.assertEqual(scoped['receipt']['hard'], {})
        cleared = runtime.chat(sid, 'Reset everything')
        self.assertEqual(cleared['selection_state']['selected_asins'], [])
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], {})
        self.assertEqual(restored['selection_state']['selected_asins'],
                         [first['products'][0]['parent_asin']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
