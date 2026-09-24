import unittest

from agentic_workflow import Agent
from shopping_agent.retrieval import color_matches
from mvp.explanations import explain_product
from mvp.server import AgentRuntime


class ColorEvidenceTests(unittest.TestCase):
    def test_variant_field_overrides_other_color_mentions(self):
        product = {'title': 'Black or blue cotton tshirt', 'details': {'Color': 'Blue'},
                   'features': ['Also available in black']}
        self.assertFalse(color_matches(product, 'black'))
        self.assertTrue(color_matches(product, 'blue'))
        match = explain_product(product, {'hard': {'color': 'black'}, 'soft': {'color': ['black']}, 'excluded': {'color': ['black']}})
        self.assertEqual([s['status'] for s in match['signals']], ['not_evidenced', 'not_evidenced', 'clear'])

    def test_title_fallback_does_not_borrow_color_from_description(self):
        self.assertTrue(color_matches({'title': 'Black cotton tshirt'}, 'black'))
        self.assertFalse(color_matches({'title': 'Cotton tshirt', 'features': ['Available in black']}, 'black'))
        self.assertFalse(color_matches({'title': 'Not black cotton tshirt'}, 'black'))
        self.assertFalse(color_matches({'title': '不是黑色的T恤'}, 'black'))
        self.assertFalse(color_matches({'color': 'blue', 'details': {'Color': 'Black'}}, 'black'))
        self.assertTrue(color_matches({'details': {'Colour': 'Grey'}}, 'gray'))
        self.assertTrue(color_matches({'details': {'Color': 'Navy'}}, 'blue'))
        self.assertFalse(color_matches({'details': {'Color': 'Blue'}}, 'navy'))
        self.assertFalse(color_matches({'title': 'Black cotton tshirt', 'details': {'Color': 'As pictured'}}, 'black'))

    def test_hard_filter_and_exclusion_use_the_same_variant_evidence(self):
        def search(query, top_k):
            return [{'product_id': key, 'score': 2-i} for i, key in enumerate(('blue', 'black'))]
        def details(ids):
            return [dict(product_id=key, found=True, title='Black or blue cotton tshirt',
                         color=key, bullet_point='Also available in black and blue') for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'black tshirt')
        self.assertEqual([p['parent_asin'] for p in first['products']], ['black'])
        second = runtime.chat(sid, 'blue instead, not black')
        self.assertEqual([p['parent_asin'] for p in second['products']], ['blue'])
        self.assertFalse(runtime.agent.errors)
