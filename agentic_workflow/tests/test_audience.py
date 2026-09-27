import unittest

from agentic_workflow import Agent
from intent_router.audience import audience_matches
from shopping_agent.requirement_parser import PrimaryRequirementParser
from shopping_agent.tests.test_model_provider import FakeProvider
from mvp.server import AgentRuntime
from mvp.audit import verify_audit


class AudienceTests(unittest.TestCase):
    def test_catalog_labels_do_not_confuse_men_with_women_or_gift_copy(self):
        for product in [{'title': "Women's black pants"}, {'title': 'Baby boys black pants'},
                        {'title': 'Men Women pants', 'details': {'Department': 'mens'}},
                        {'title': 'Black pants', 'description': 'Great gift for men'},
                        {'title': 'Unisex kids pants'}, {'title': 'Black pants'}]:
            self.assertFalse(audience_matches(product, 'men'), product)
        for product in [{'title': "Men's black pants"}, {'title': 'Unisex black pants'},
                        {'title': 'Black pants', 'details': {'Department': 'mens'}}]:
            self.assertTrue(audience_matches(product, 'men'), product)

    def test_model_can_write_audience_and_normalize_man(self):
        provider = FakeProvider({'updates': [{'slot': 'audience', 'operation': 'set',
                                'values': ['men'], 'constraint_type': 'hard', 'evidence': "I'm a man"}]})
        result = PrimaryRequirementParser(provider).enhance("I'm a man", ())
        self.assertTrue(result.replaces_rules)
        provider.data['updates'][0]['evidence'] = 'women'
        self.assertFalse(PrimaryRequirementParser(provider).enhance('women', ()).replaces_rules)

    def test_correction_filter_category_switch_and_undo(self):
        catalog = {
            'w': "Women's blue cotton dress XL",
            'g': "Girls blue cotton dress XL",
            'wp': "Women's black pants XL",
            'b': 'Baby boys black pants XL',
            'm': "Men's black pants XL",
            'u': 'Unisex black pants XL',
            'unknown': 'Black pants XL',
        }
        calls = []
        def search(query, top_k):
            calls.append(query)
            return [{'product_id': key, 'score': 20-i} for i, key in enumerate(catalog)]
        def details(ids):
            return [{'product_id': key, 'found': True, 'title': catalog[key], 'price': 20} for key in ids]
        r = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        s = r.new_session()['session_id']
        r.chat(s, 'I need a blue cotton dress')
        out = r.chat(s, "wait but I'm not a girl! I want XL size")
        self.assertEqual(out['receipt']['excluded']['audience'], ['girls'])
        self.assertNotIn('g', [p['parent_asin'] for p in out['products']])
        out = r.chat(s, "NONONO I'm a men, I want men's stuff")
        self.assertEqual(out['receipt']['hard']['audience'], 'men')
        self.assertEqual(out['products'], [])
        out = r.chat(s, 'yes please restart the search for men')
        self.assertEqual(out['receipt']['hard']['audience'], 'men')
        self.assertTrue(out['receipt']['search_execution']['retrieved'])
        self.assertFalse(out['receipt']['search_execution']['requirements_reset'])
        out = r.chat(s, "let me have men's black pants instead")
        self.assertEqual(out['receipt']['hard']['audience'], 'men')
        self.assertEqual({p['parent_asin'] for p in out['products']}, {'m', 'u'})
        switched = r.chat(s, 'blue shirts instead')
        self.assertEqual(switched['receipt']['hard']['audience'], 'men')
        restored = r.chat(s, 'undo')
        self.assertEqual(restored['receipt']['hard']['audience'], 'men')
        self.assertEqual({p['parent_asin'] for p in restored['products']}, {'m', 'u'})
        count = len(calls)
        recap = r.chat(s, 'What are my preferences?')
        self.assertFalse(recap['receipt']['search_execution']['retrieved'])
        self.assertEqual(len(calls), count)
        self.assertEqual(verify_audit(r.audit(s)), [])
