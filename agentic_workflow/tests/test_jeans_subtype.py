import unittest

from agentic_workflow import Agent
from mvp.audit import verify_audit
from mvp.server import AgentRuntime
from shopping_agent.retrieval import subtype_matches


class JeansSubtypeTests(unittest.TestCase):
    @staticmethod
    def runtime():
        catalog = {
            'JEANS': {'title': 'Blue stretch jeans', 'color': 'blue'},
            'CHINOS': {'title': 'Blue chino pants', 'color': 'blue',
                       'bullet_point': 'Pairs well with jeans'},
            'TROUSERS': {'title': 'Blue work trousers', 'color': 'blue'},
            'BLACK': {'title': 'Black denim jeans', 'color': 'black'},
        }

        def search(query, top_k):
            return [{'product_id': key, 'score': 10 - i}
                    for i, key in enumerate(catalog)][:top_k]

        def details(ids):
            return [{'product_id': key, 'found': True, **catalog[key]} for key in ids]

        return AgentRuntime(Agent(search_function=search, details_function=details,
                                  trace_enabled=True), orchestration_mode='adaptive')

    def test_subtype_needs_title_or_taxonomy_not_description_mention(self):
        self.assertTrue(subtype_matches({'title': 'Blue jeans', 'categories': ['Pants']}, 'jeans'))
        self.assertTrue(subtype_matches({'title': 'Blue denim pants', 'categories': ['Jeans']}, 'jeans'))
        self.assertFalse(subtype_matches({'title': 'Blue chino pants', 'categories': ['Pants'],
                                          'description': 'Pairs well with jeans'}, 'jeans'))

    def test_jeans_request_excludes_other_pants_and_can_be_broadened(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        jeans = runtime.chat(sid, 'I need blue jeans')
        self.assertEqual(jeans['receipt']['hard']['category'], 'pants')
        self.assertEqual(jeans['receipt']['hard']['subtype'], 'jeans')
        self.assertEqual([p['parent_asin'] for p in jeans['products']], ['JEANS'])
        self.assertIn('blue jeans', jeans['assistant']['message'].lower())
        self.assertEqual(jeans['products'][0]['match']['hard_supported'],
                         jeans['products'][0]['match']['hard_total'])

        broadened = runtime.chat(sid, 'Show me pants instead')
        self.assertNotIn('subtype', broadened['receipt']['hard'])
        self.assertIn('CHINOS', [p['parent_asin'] for p in broadened['products']])
        restored = runtime.chat(sid, 'Undo')
        self.assertEqual(restored['receipt']['hard']['subtype'], 'jeans')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_deferred_blue_jeans_keep_subtype_when_selected_later(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'A black dress and blue jeans')
        self.assertEqual(first['receipt']['question']['options'], ['dress', 'pants'])
        runtime.chat(sid, 'Dress')
        jeans = runtime.chat(sid, 'Now the jeans')
        self.assertEqual(jeans['receipt']['hard']['subtype'], 'jeans')
        self.assertEqual(jeans['receipt']['hard']['color'], 'blue')
        self.assertEqual([p['parent_asin'] for p in jeans['products']], ['JEANS'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
