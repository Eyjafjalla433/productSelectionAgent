import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.server import AgentRuntime


class CorrectionAcknowledgementTests(unittest.TestCase):
    @staticmethod
    def runtime(blue_size=True):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 20 - i} for i in range(12)]

        def details(ids):
            return [dict(product_id=i, found=True, price=20,
                         title=('Black cotton tshirt size M' if int(i) < 6
                                else 'Blue cotton tshirt size M' if blue_size
                                else 'Blue cotton tshirt')) for i in ids]

        return AgentRuntime(Agent(search_function=search, details_function=details,
                                  trace_enabled=True), orchestration_mode='adaptive')

    def test_one_correction_names_retained_details_and_undo(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt size M around $30')
        changed = runtime.chat(sid, 'Actually blue, but keep cotton and size M')
        message = changed['assistant']['message']
        self.assertIn('I changed the color from black to blue.', message)
        self.assertIn('I kept cotton', message)
        self.assertIn('size M', message)
        self.assertIn('roughly $30 budget', message)
        self.assertIn('You can undo that change.', message)
        self.assertEqual(changed['receipt']['hard']['color'], 'blue')
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(restored['receipt']['soft'], first['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_two_changes_are_reported_without_false_retention(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black cotton tshirt')
        changed = runtime.chat(sid, 'Actually blue, no longer cotton')
        self.assertGreaterEqual(len(changed['receipt']['state_changes']), 2)
        self.assertNotIn('I kept cotton', changed['assistant']['message'])
        self.assertIn('I changed the color from black to blue.', changed['assistant']['message'])
        self.assertIn('I dropped the cotton material requirement.', changed['assistant']['message'])
        self.assertIn('undo these changes together', changed['assistant']['message'])

    def test_three_detail_correction_and_one_step_undo(self):
        runtime = self.runtime(blue_size=False)
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black cotton tshirt size M around $30')
        changed = runtime.chat(sid, 'Actually blue, size L, around $40')
        self.assertEqual(len(changed['receipt']['state_changes']), 3)
        self.assertEqual(changed['receipt']['hard']['color'], 'blue')
        self.assertEqual(changed['receipt']['hard']['size'], 'l')
        self.assertEqual(changed['receipt']['soft']['budget_target'], ['40.0'])
        text = changed['assistant']['message']
        self.assertIn('color from black to blue', text)
        self.assertIn('size from M to L', text)
        self.assertIn('budget target from around $30 to around $40', text)
        self.assertIn('I kept cotton', text)
        self.assertIn('undo these changes together', text)
        self.assertIn('I cannot verify a match', text)
        restored = runtime.chat(sid, 'undo')
        self.assertEqual(restored['receipt']['hard'], first['receipt']['hard'])
        self.assertEqual(restored['receipt']['soft'], first['receipt']['soft'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_correction_is_acknowledged_even_when_no_product_is_verified(self):
        runtime = self.runtime(blue_size=False)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'black cotton tshirt size M around $30')
        changed = runtime.chat(sid, 'Actually blue, but keep cotton and size M')
        self.assertEqual(changed['products'], [])
        self.assertEqual(changed['receipt']['hard']['color'], 'blue')
        self.assertIn('I changed the color from black to blue.', changed['assistant']['message'])
        self.assertIn('I cannot verify a match', changed['assistant']['message'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
