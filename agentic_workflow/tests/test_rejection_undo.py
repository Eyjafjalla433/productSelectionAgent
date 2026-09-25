import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.server import AgentRuntime


class RejectionUndoTests(unittest.TestCase):
    @staticmethod
    def runtime():
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 20-i} for i in range(10)]

        def details(ids):
            return [dict(product_id=i, found=True, title='Cotton tshirt') for i in ids]

        agent = Agent(search_function=search, details_function=details, trace_enabled=True)
        return AgentRuntime(agent, orchestration_mode='adaptive')

    def test_undo_and_redo_rejection_survive_changed_displayed_ranks(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        rejected_id = first['products'][1]['parent_asin']
        refreshed = runtime.chat(sid, 'Reject #2 and show me more')
        self.assertNotIn(rejected_id, [p['parent_asin'] for p in refreshed['products']])
        self.assertTrue(refreshed['selection_state']['can_undo_rejection'])

        restored = runtime.chat(sid, 'undo rejection')
        self.assertEqual(restored['receipt']['rejection_undo'], 'applied')
        self.assertEqual(restored['selection_state']['rejected_asins'], [])
        self.assertTrue(restored['selection_state']['can_redo_rejection'])
        self.assertEqual([p['parent_asin'] for p in restored['products']],
                         [p['parent_asin'] for p in refreshed['products']])
        self.assertEqual(restored['receipt']['hard'], refreshed['receipt']['hard'])
        self.assertNotIn('2_retrieval', [e['stage'] for e in runtime.agent.get_trace(sid, 3)['events']])

        redone = runtime.chat(sid, 'redo rejection')
        self.assertEqual(redone['receipt']['rejection_redo'], 'applied')
        self.assertIn(rejected_id, redone['selection_state']['rejected_asins'])
        self.assertEqual([p['parent_asin'] for p in redone['products']],
                         [p['parent_asin'] for p in refreshed['products']])

        runtime.chat(sid, 'undo rejection')
        later = runtime.chat(sid, 'show me more')
        self.assertIn(rejected_id, [p['parent_asin'] for p in later['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_plain_undo_does_not_undo_feedback(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        rejected_id = first['products'][1]['parent_asin']
        runtime.chat(sid, 'Reject #2')
        requirement_undo = runtime.chat(sid, 'undo')
        self.assertIn(rejected_id, requirement_undo['selection_state']['rejected_asins'])
        feedback_undo = runtime.chat(sid, 'undo rejection')
        self.assertNotIn(rejected_id, feedback_undo['selection_state']['rejected_asins'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_undo_rejection_leaves_unrelated_shortlist_selection(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        reply = runtime.chat(sid, "Keep #2 and I don't like #3")
        self.assertEqual(reply['selection_state']['selected_asins'], [first['products'][1]['parent_asin']])
        restored = runtime.chat(sid, 'undo rejection')
        self.assertEqual(restored['selection_state']['selected_asins'], [first['products'][1]['parent_asin']])
        self.assertEqual(restored['selection_state']['rejected_asins'], [])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_empty_rejection_history_is_a_no_op(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        reply = runtime.chat(sid, 'undo rejection')
        self.assertEqual(reply['receipt']['rejection_undo'], 'empty')
        self.assertEqual([p['parent_asin'] for p in reply['products']],
                         [p['parent_asin'] for p in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_batch_rejection_undoes_as_one_feedback_edit(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'tshirt')
        rejected = {first['products'][1]['parent_asin'], first['products'][2]['parent_asin']}
        reply = runtime.chat(sid, 'Reject #2 and #3')
        self.assertEqual(set(reply['selection_state']['rejected_asins']), rejected)
        undone = runtime.chat(sid, 'undo rejection')
        self.assertEqual(undone['selection_state']['rejected_asins'], [])
        redone = runtime.chat(sid, 'redo rejection')
        self.assertEqual(set(redone['selection_state']['rejected_asins']), rejected)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_category_change_clears_old_rejection_history(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'tshirt')
        runtime.chat(sid, 'Reject #2')
        changed = runtime.chat(sid, 'switch to shoes')
        self.assertFalse(changed['selection_state']['can_undo_rejection'])
        reply = runtime.chat(sid, 'undo rejection')
        self.assertEqual(reply['receipt']['rejection_undo'], 'empty')
        self.assertEqual(reply['receipt']['hard']['category'], 'shoes')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
