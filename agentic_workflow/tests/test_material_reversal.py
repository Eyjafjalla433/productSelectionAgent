import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.server import AgentRuntime


class MaterialReversalTests(unittest.TestCase):
    @staticmethod
    def runtime():
        catalog = {
            'cotton': 'Blue cotton shirt',
            'linen': 'Blue linen shirt',
            'silk': 'Blue silk shirt',
            'red': 'Red linen shirt',
        }

        def search(query, top_k):
            return [{'product_id': key, 'score': 20 - i}
                    for i, key in enumerate(catalog)]

        def details(ids):
            return [dict(product_id=key, found=True, price=20, title=catalog[key])
                    for key in ids]

        return AgentRuntime(Agent(search_function=search, details_function=details,
                                  trace_enabled=True), orchestration_mode='adaptive')

    def test_reversal_updates_receipt_results_and_undo(self):
        for wording in (
            "wait I don't want cotton anymore, could you recommend me some other materials?",
            "I don't want cotton anymore",
            "I don’t want cotton anymore!",
            'I do not want cotton any more.',
            'I no longer want cotton',
            "I don't want cotton anymore but I want linen",
        ):
            with self.subTest(wording=wording):
                runtime = self.runtime()
                sid = runtime.new_session()['session_id']
                before = runtime.chat(sid, 'I want a cotton blue shirt')
                self.assertEqual(before['receipt']['hard']['material'], 'cotton')
                self.assertEqual([p['parent_asin'] for p in before['products']], ['cotton'])
                after = runtime.chat(sid, wording)
                self.assertEqual(after['receipt']['hard']['category'], 'shirt')
                self.assertEqual(after['receipt']['hard']['color'], 'blue')
                self.assertNotEqual(after['receipt']['hard'].get('material'), 'cotton')
                self.assertEqual(after['receipt']['excluded']['material'], ['cotton'])
                expected = {'linen'} if 'want linen' in wording else {'linen', 'silk'}
                self.assertEqual({p['parent_asin'] for p in after['products']}, expected)
                restored = runtime.chat(sid, 'undo')
                self.assertEqual(restored['receipt']['hard'], before['receipt']['hard'])
                self.assertEqual(restored['receipt']['excluded'], before['receipt']['excluded'])
                self.assertEqual([p['parent_asin'] for p in restored['products']], ['cotton'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_indifference_does_not_ban_cotton(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I want a cotton blue shirt')
        after = runtime.chat(sid, "I don't care about material anymore")
        self.assertNotIn('material', after['receipt']['hard'])
        self.assertNotIn('material', after['receipt']['excluded'])
        self.assertEqual({p['parent_asin'] for p in after['products']},
                         {'cotton', 'linen', 'silk'})
