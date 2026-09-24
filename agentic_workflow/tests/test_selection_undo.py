import unittest

from agentic_workflow import Agent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit


class SelectionUndoTests(unittest.TestCase):
    def test_saved_product_is_rechecked_after_requirements_change(self):
        first = self.chat('Select #1')
        before = first['selection_state']['selected_products'][0]
        self.assertTrue(any(s['slot'] == 'color' and s['status'] == 'supported' for s in before['match']['signals']))
        changed = self.chat('blue instead')
        self.assertEqual(changed['selection_state']['selected_asins'], ['0'])
        saved = changed['selection_state']['selected_products'][0]
        self.assertTrue(any(s['slot'] == 'color' and s['value'] == 'blue' and s['status'] != 'supported' for s in saved['match']['signals']))
        self.assertFalse(changed['selection_state']['finalized'])
        restored = self.chat('undo')
        saved = restored['selection_state']['selected_products'][0]
        self.assertTrue(any(s['slot'] == 'color' and s['status'] == 'supported' for s in saved['match']['signals']))

    def test_redo_restores_edit_as_draft_and_branching_clears_redo(self):
        self.chat('Select #1 and #2')
        self.chat('Remove #2')
        undone = self.chat('Undo selection')
        self.assertTrue(undone['selection_state']['can_redo_selection'])
        self.chat('Finalize my selection')
        redone = self.chat('Redo selection')
        self.assertEqual(redone['selection_state']['selected_asins'], ['0'])
        self.assertFalse(redone['selection_state']['finalized'])
        self.assertFalse(redone['selection_state']['can_redo_selection'])
        self.chat('Undo selection')
        self.chat('Select #3')
        branched = self.chat('Redo selection')
        self.assertEqual(branched['selection_state']['selected_asins'], ['0', '1', '2'])
        self.assertFalse(branched['selection_state']['can_redo_selection'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(verify_audit(self.runtime.audit(self.sid)), [])

    def test_noop_keeps_redo_but_requirement_change_discards_it(self):
        self.chat('Select #1')
        self.chat('Undo selection')
        self.chat('Clear shortlist')
        result = self.chat('Redo shortlist')
        self.assertEqual(result['selection_state']['selected_asins'], ['0'])
        self.chat('Undo selection')
        self.chat('blue instead')
        result = self.chat('Redo selection')
        self.assertFalse(result['selection_state']['can_redo_selection'])
        self.assertEqual(result['selection_state']['selected_asins'], [])

    def test_restore_includes_cached_details_after_browsing_another_batch(self):
        calls = []
        def search(query, top_k):
            calls.append(query)
            return [{'product_id': str(i), 'score': 40-i} for i in range(30)]
        def details(ids):
            return [dict(product_id=i, found=True, title=f'Black cotton tshirt model {i}') for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        next_batch = runtime.chat(sid, 'Show me more')
        selected = first['products'][0]
        self.assertNotIn(selected['parent_asin'], [p['parent_asin'] for p in next_batch['products']])
        runtime.update_selection(sid, clear=True)
        count = len(calls)
        restored = runtime.chat(sid, 'Undo selection')
        cached = restored['selection_state']['selected_products']
        self.assertEqual(cached[0]['parent_asin'], selected['parent_asin'])
        self.assertEqual(cached[0]['title'], selected['title'])
        self.assertIsNone(cached[0]['price'])
        self.assertEqual(len(calls), count)
        self.assertEqual([p['parent_asin'] for p in restored['products']], [p['parent_asin'] for p in next_batch['products']])

    def setUp(self):
        self.calls = []
        def search(query, top_k):
            self.calls.append(query)
            return [{'product_id': str(i), 'score': 3-i} for i in range(3)]
        def details(ids):
            return [dict(product_id=i, found=True, title='Black cotton tshirt') for i in ids]
        self.runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        self.sid = self.runtime.new_session()['session_id']
        self.first = self.chat('black tshirt')

    def chat(self, message):
        return self.runtime.chat(self.sid, message)

    def test_chat_removal_undo_restores_choices_without_search_or_requirement_change(self):
        self.chat('Select #1 and #2')
        removed = self.chat('Remove #2')
        self.assertEqual(removed['selection_state']['selected_asins'], ['0'])
        result = self.chat('Undo selection')
        self.assertEqual(result['selection_state']['selected_asins'], ['0', '1'])
        self.assertFalse(result['selection_state']['finalized'])
        self.assertEqual(result['receipt']['hard'], self.first['receipt']['hard'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(verify_audit(self.runtime.audit(self.sid)), [])

    def test_ui_clear_and_undo_return_draft_not_final_confirmation(self):
        self.runtime.update_selection(self.sid, parent_asin='0', selected=True)
        self.chat('Finalize my selection')
        self.runtime.update_selection(self.sid, clear=True)
        result = self.chat('Undo shortlist')
        self.assertEqual(result['selection_state']['selected_asins'], ['0'])
        self.assertFalse(result['selection_state']['finalized'])
        self.assertEqual(len(self.calls), 1)

    def test_noop_does_not_consume_history(self):
        self.chat('Select #1')
        self.chat('Select #1')
        self.assertEqual(self.chat('Undo selection')['selection_state']['selected_asins'], [])
        result = self.chat('Undo selection')
        self.assertFalse(result['selection_state']['can_undo_selection'])
        self.assertIn("isn't a shortlist edit", result['assistant']['message'])

    def test_requirement_change_or_rejection_clears_history(self):
        self.chat('Select #1')
        self.chat('Remove #1')
        self.chat('blue instead')
        result = self.chat('Undo selection')
        self.assertEqual(result['selection_state']['selected_asins'], [])
        self.assertFalse(result['selection_state']['can_undo_selection'])
        self.chat('black instead')
        self.chat('Select #1')
        self.chat('Reject #1')
        result = self.chat('Undo selection')
        self.assertEqual(result['selection_state']['selected_asins'], [])
        self.assertFalse(result['selection_state']['can_undo_selection'])
