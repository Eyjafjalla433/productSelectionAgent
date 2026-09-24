import unittest

import agentic_workflow
from mvp.explanations import explain_product
from mvp.shopping_guide import preference_tradeoff


class PreferenceExplanationTests(unittest.TestCase):
    def product(self, title, receipt):
        return {'match': explain_product({'title': title}, receipt)}

    def test_pure_cotton_evidence_matches_filter_not_token_overlap(self):
        receipt = {'hard': {'material': '100% cotton'}}
        pure = self.product('Pure cotton tshirt', receipt)
        self.assertEqual(pure['match']['signals'][0]['status'], 'supported')
        blend = self.product('100% cotton tee; heather polyester blend', receipt)
        self.assertNotEqual(blend['match']['signals'][0]['status'], 'supported')
        soft = self.product('100% cotton tee; heather polyester blend', {'soft': {'material': ['100% cotton']}})
        self.assertIn('could not confirm', preference_tradeoff([soft]))

    def test_budget_number_in_title_is_not_price_evidence(self):
        product = self.product('30/1 cotton tshirt', {'soft': {'budget_target': [30]}})
        self.assertEqual(product['match']['signals'][0]['status'], 'unknown')
        self.assertEqual(preference_tradeoff([product]), '')

    def test_missing_preference_is_not_claimed_as_product_defect(self):
        receipt = {'soft': {'color': ['black']}}
        unknown = self.product('Cotton tshirt', receipt)
        text = preference_tradeoff([unknown])
        self.assertIn('black', text)
        self.assertIn('could not confirm', text)
        self.assertNotIn('不是black', text)
        matched = self.product('Black cotton tshirt', receipt)
        self.assertEqual(preference_tradeoff([matched]), '')
        self.assertIn('1 of these', preference_tradeoff([matched, unknown]))
        self.assertIn('alternatives', preference_tradeoff([unknown], 'en'))

    def test_no_unsolicited_caveats_without_preferences(self):
        product = self.product('Cotton tshirt', {'hard': {'material': 'cotton'}})
        self.assertEqual(preference_tradeoff([product]), '')
