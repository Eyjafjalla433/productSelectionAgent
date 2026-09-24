import unittest

from agentic_workflow import Agent
from mvp.control_intent import parse_control_intent
from mvp.server import AgentRuntime
from mvp.audit import verify_audit


class ConfirmationBoundaryTests(unittest.TestCase):
    def test_negated_edits_are_not_confused_with_negative_product_feedback(self):
        for message in ("Don't remove #2", 'Do not clear my shortlist',
                        'Please do not select #1', 'Never export my selection',
                        "Don't reject #2", "Don't compare #1 and #2"):
            self.assertEqual(parse_control_intent(message).action, 'retain')
        self.assertEqual(parse_control_intent("I don't like #2").action, 'reject')
        self.assertEqual(parse_control_intent('Remove #2').action, 'remove')

    def test_postponing_retains_draft_results_and_requirements_without_search(self):
        calls = []
        def search(query, top_k):
            calls.append(query)
            return [{'product_id': 'one', 'score': 1}]
        def details(ids):
            return [dict(product_id=i, found=True, title='Black cotton tshirt') for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        before = runtime.agent.memory.snapshot(sid)
        count = len(calls)
        for message in ("Don't finalize yet", "I'm not ready to finalize",
                        'If it is under $30, finalize my selection',
                        'Finalize my selection after checking the price'):
            result = runtime.chat(sid, message)
            self.assertEqual(result['receipt']['pre_reason'], 'selection_hold')
            self.assertFalse(result['selection_state']['finalized'])
            self.assertEqual(result['selection_state']['selected_asins'], ['one'])
            self.assertEqual([p['parent_asin'] for p in result['products']], [p['parent_asin'] for p in first['products']])
            self.assertEqual(runtime.agent.memory.snapshot(sid).hard_constraints, before.hard_constraints)
            self.assertEqual(len(calls), count)
        runtime.chat(sid, 'Finalize my selection')
        kept = runtime.chat(sid, "Don't remove #1")
        self.assertTrue(kept['selection_state']['finalized'])
        self.assertEqual(kept['selection_state']['selected_asins'], ['one'])
        self.assertEqual(kept['receipt']['state_changes'], [])
        self.assertEqual(len(calls), count)
        self.assertNotIn('handoff', kept)
        kept = runtime.chat(sid, 'Do not clear my shortlist')
        self.assertTrue(kept['selection_state']['finalized'])
        self.assertEqual(kept['selection_state']['selected_asins'], ['one'])
        reopened = runtime.chat(sid, 'Do not confirm my shortlist')
        self.assertFalse(reopened['selection_state']['finalized'])
        self.assertEqual(reopened['selection_state']['selected_asins'], ['one'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_only_explicit_shortlist_commands_finalize(self):
        for message in ('Finalize', 'Finalize my selection.', 'Please confirm my shortlist',
                        'Could you please finalize my selection?', 'Finish the selection',
                        '确认最终选择', '完成选择'):
            self.assertEqual(parse_control_intent(message).action, 'finalize', message)
        for message in ("Don't finalize my selection", 'Do not confirm my shortlist',
                        'I am not ready to finalize', 'Can you confirm the price?',
                        'Confirm that #2 is cotton', 'If it is under $30, finalize my selection',
                        'Finalize my selection after checking the price', 'Finish looking for shoes',
                        '不要确认最终选择'):
            control = parse_control_intent(message)
            self.assertTrue(control is None or control.action != 'finalize', message)

    def test_negated_command_cannot_finalize_existing_shortlist(self):
        def search(query, top_k):
            return [{'product_id': 'one', 'score': 1}]
        def details(ids):
            return [dict(product_id=i, found=True, title='Black cotton tshirt') for i in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black tshirt')
        runtime.chat(sid, 'Select #1')
        for message in ("Don't finalize my selection", 'Can you confirm the price?',
                        'If it is under $30, finalize my selection'):
            result = runtime.chat(sid, message)
            self.assertFalse(result['selection_state']['finalized'])
            self.assertEqual(result['selection_state']['selected_asins'], ['one'])
        final = runtime.chat(sid, 'Please finalize my selection')
        self.assertTrue(final['selection_state']['finalized'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
