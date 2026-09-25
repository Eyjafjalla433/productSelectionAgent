import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.server import AgentRuntime


class RankedColorPreferenceTests(unittest.TestCase):
    @staticmethod
    def runtime():
        calls = []

        def search(query, top_k):
            calls.append(query)
            return [{'product_id': item, 'score': 10 - index}
                    for index, item in enumerate(('WHITE', 'BLACK', 'UNKNOWN'))]

        def details(ids):
            titles = {'WHITE': 'White cotton T-shirt',
                      'BLACK': 'Black cotton T-shirt',
                      'UNKNOWN': 'Cotton T-shirt'}
            return [dict(product_id=item, found=True, title=titles[item]) for item in ids]

        return AgentRuntime(Agent(search_function=search, details_function=details,
                                  trace_enabled=True), orchestration_mode='adaptive'), calls

    def test_first_choice_leads_without_excluding_acceptable_color(self):
        runtime, calls = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a tshirt')
        self.assertTrue(first['products'])
        result = runtime.chat(sid, 'I prefer black, but white is okay too')
        choices = runtime.agent.memory.snapshot(sid).soft_preferences['color']
        self.assertEqual([choice.value for choice in choices], ['black', 'white'])
        self.assertGreater(choices[0].weight, choices[1].weight)
        self.assertEqual(result['receipt']['soft']['color'], ['black', 'white'])
        self.assertEqual([item['parent_asin'] for item in result['products']],
                         ['BLACK', 'WHITE', 'UNKNOWN'])
        self.assertIn('black', calls[-1])
        self.assertIn('white', calls[-1])
        self.assertIn('white also okay', result['assistant']['message'])
        self.assertEqual(result['receipt']['ordered_preferences']['color'],
                         {'preferred': 'black', 'also_acceptable': 'white'})
        explained = runtime.chat(sid, 'Why is #1 first?')
        self.assertIn('first choice', explained['assistant']['message'])
        recap = runtime.chat(sid, 'What are my preferences?')
        self.assertIn('black first; white also okay', recap['assistant']['message'])
        undone = runtime.chat(sid, 'Undo')
        self.assertNotIn('color', undone['receipt']['soft'])
        self.assertEqual([item['parent_asin'] for item in undone['products']],
                         [item['parent_asin'] for item in first['products']])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_equal_alternatives_keep_search_order(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        result = runtime.chat(sid, 'Black or white is fine')
        self.assertEqual(result['receipt']['soft']['color'], ["('black', 'white')"])
        self.assertEqual([item['parent_asin'] for item in result['products']][:2],
                         ['WHITE', 'BLACK'])
        self.assertEqual(result['receipt']['ranking_method'], 'search_tool')

    def test_primary_color_comes_from_utterance_not_lexicon_order(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a tshirt; I prefer white, but black is okay')
        choices = runtime.agent.memory.snapshot(sid).soft_preferences['color']
        self.assertEqual([choice.value for choice in choices], ['white', 'black'])
        self.assertEqual([item['parent_asin'] for item in result['products']][:2],
                         ['WHITE', 'BLACK'])
        self.assertIn('black also okay', result['assistant']['message'])

    def test_first_mentioned_fallback_does_not_steal_priority(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I need a tshirt; white is okay, but I prefer black')
        self.assertEqual(result['receipt']['ordered_preferences']['color'],
                         {'preferred': 'black', 'also_acceptable': 'white'})
        self.assertEqual(result['products'][0]['parent_asin'], 'BLACK')

    def test_reversing_color_priority_is_one_undoable_edit(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        first = runtime.chat(sid, 'I prefer black, but white is okay too')
        changed = runtime.chat(sid, 'Actually, I prefer white, but black is okay too')
        self.assertEqual(changed['receipt']['ordered_preferences']['color']['preferred'], 'white')
        self.assertTrue(changed['receipt']['state_changes'])
        self.assertEqual(changed['products'][0]['parent_asin'], 'WHITE')
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['ordered_preferences']['color']['preferred'], 'black')
        self.assertEqual([item['parent_asin'] for item in restored['products']],
                         [item['parent_asin'] for item in first['products']])
        redone = runtime.chat(sid, 'Redo')
        self.assertEqual(redone['receipt']['ordered_preferences']['color']['preferred'], 'white')
        self.assertEqual(redone['products'][0]['parent_asin'], 'WHITE')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_natural_first_choice_phrasings_keep_fallback_soft(self):
        for message in ('White first, black is okay',
                        'White is my first choice; black is still fine',
                        'Make white the priority, black is acceptable'):
            with self.subTest(message=message):
                runtime, _ = self.runtime()
                sid = runtime.new_session()['session_id']
                runtime.chat(sid, 'I need a tshirt')
                result = runtime.chat(sid, message)
                self.assertEqual(result['receipt']['ordered_preferences']['color'],
                                 {'preferred': 'white', 'also_acceptable': 'black'})
                self.assertNotIn('color', result['receipt']['hard'])
                self.assertEqual(result['products'][0]['parent_asin'], 'WHITE')

    def test_shopper_can_make_alternatives_equal_then_undo(self):
        runtime, _ = self.runtime()
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a tshirt')
        runtime.chat(sid, 'I prefer black, but white is okay too')
        equal = runtime.chat(sid, 'Actually, black or white is fine')
        self.assertEqual(equal['receipt']['ordered_preferences'], {})
        self.assertEqual(equal['products'][0]['parent_asin'], 'WHITE')
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['ordered_preferences']['color']['preferred'], 'black')
        self.assertEqual(restored['products'][0]['parent_asin'], 'BLACK')
