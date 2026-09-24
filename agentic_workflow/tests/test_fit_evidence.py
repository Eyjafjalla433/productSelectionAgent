import unittest

from agentic_workflow import Agent
from shopping_agent.retrieval import style_matches
from mvp.explanations import explain_product
from mvp.server import AgentRuntime
from mvp.shopping_guide import describe, build_shopping_guide


class FitEvidenceTests(unittest.TestCase):
    def test_summary_prefers_structured_fit_and_preserves_its_evidence(self):
        product = dict(rank=1, parent_asin='fit', title='Fitted cotton tee')
        source = dict(product, details={'Fit Type': 'Loose Fit'})
        notes = describe(product, source)
        self.assertIn('Loose fit', notes['feature'])
        self.assertNotIn('Slim fit', notes['feature'])
        self.assertIn({'label': 'Loose fit', 'group': 'design', 'quote': 'Loose Fit'}, notes['evidence'])
        product['shopper_notes'] = notes
        guide = build_shopping_guide([product])
        self.assertEqual(guide['top_three'][0], notes)
        guide['top_three'][0]['evidence'].clear()
        self.assertTrue(notes['evidence'])

    def test_summary_does_not_invent_fit_from_negated_or_conflicting_text(self):
        for title in ('Not very fitted tee', 'Regular fit and slim fit tee', 'Loose fit and fitted tee'):
            notes = describe(dict(rank=1, parent_asin='fit', title=title))
            self.assertFalse(any(e['label'] in {'Slim fit', 'Loose fit', 'Regular fit'} for e in notes['evidence']))

    def test_summary_understands_hyphenated_fit(self):
        notes = describe(dict(rank=1, parent_asin='fit', title='Relaxed-fit tee'))
        self.assertIn('Loose fit', notes['feature'])

    def test_fit_equivalents_and_negation(self):
        self.assertTrue(style_matches({'title': 'Fitted cotton tee'}, 'slim fit'))
        self.assertTrue(style_matches({'title': 'Relaxed-fit tee'}, 'loose fit'))
        self.assertTrue(style_matches({'title': 'Oversized tee'}, 'loose fit'))
        self.assertFalse(style_matches({'title': 'Not fitted cotton tee'}, 'slim fit'))
        self.assertFalse(style_matches({'title': 'Cotton tee', 'store': 'Fitted'}, 'slim fit'))
        self.assertFalse(style_matches({'title': 'Fitted tee', 'details': {'Fit Type': 'Loose Fit'}}, 'slim fit'))
        self.assertTrue(style_matches({'title': 'Fitted tee', 'details': {'Fit Type': 'Loose Fit'}}, 'loose fit'))

    def test_explanation_uses_same_fit_evidence(self):
        result = explain_product({'title': 'Not fitted cotton tee'}, {'excluded': {'style': ['slim fit']}})
        self.assertEqual(result['signals'][0]['status'], 'clear')
        result = explain_product({'title': 'Relaxed-fit cotton tee'}, {'soft': {'style': ['loose fit']}})
        self.assertEqual(result['signals'][0]['status'], 'supported')

    def test_chinese_exclusion_filters_fitted_but_not_negated_fitted(self):
        titles = {'fitted': 'Fitted cotton tshirt', 'relaxed': 'Relaxed fit cotton tshirt',
                  'negated': 'Not fitted cotton tshirt'}
        def search(query, top_k):
            return [{'product_id': key, 'score': 3-i} for i, key in enumerate(titles)]
        def details(ids):
            return [dict(product_id=key, found=True, title=titles[key]) for key in ids]
        runtime = AgentRuntime(Agent(search_function=search, details_function=details, trace_enabled=True), orchestration_mode='adaptive')
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, '我想要T恤，不要太紧身')
        self.assertEqual({p['parent_asin'] for p in result['products']}, {'relaxed', 'negated'})
        notes = {p['parent_asin']: p['shopper_notes'] for p in result['products']}
        self.assertIn('Loose fit', notes['relaxed']['feature'])
        self.assertNotIn('Slim fit', notes['negated']['feature'])
        self.assertFalse(runtime.agent.errors)
