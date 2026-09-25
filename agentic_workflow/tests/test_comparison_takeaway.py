import unittest

from mvp.shopping_guide import build_shopping_guide


class ComparisonTakeawayTests(unittest.TestCase):
    def test_top_three_calls_out_distinct_listed_sizes_without_stock_claim(self):
        products = [
            {'rank': 1, 'parent_asin': 'A', 'title': 'Radiohead Bear Cotton T-shirt Medium Black', 'price': None},
            {'rank': 2, 'parent_asin': 'B', 'title': 'Radiohead Backwards Cotton T-shirt Small Black', 'price': None},
            {'rank': 3, 'parent_asin': 'C', 'title': 'Pink Floyd Cotton T-shirt Large Black', 'price': None},
        ]
        guide = build_shopping_guide(products)
        takeaway = guide['comparison_takeaway']
        self.assertIn('#1 M', takeaway)
        self.assertIn('#2 S', takeaway)
        self.assertIn('#3 L', takeaway)
        self.assertIn('listing titles', takeaway)
        self.assertIn('confirm', takeaway.lower())
        self.assertNotIn('in stock', takeaway.lower())

    def test_no_size_split_means_no_size_takeaway(self):
        products = [
            {'rank': rank, 'parent_asin': str(rank), 'title': 'Black cotton T-shirt', 'price': None}
            for rank in range(1, 4)
        ]
        self.assertEqual(build_shopping_guide(products)['comparison_takeaway'], '')

    def test_explicit_fit_split_is_compared_without_promising_personal_fit(self):
        products = [
            {'rank': 1, 'parent_asin': 'A', 'title': 'Everyday tee slim fit', 'price': None},
            {'rank': 2, 'parent_asin': 'B', 'title': 'Everyday tee loose fit', 'price': None},
            {'rank': 3, 'parent_asin': 'C', 'title': 'Everyday tee regular fit', 'price': None},
        ]
        takeaway = build_shopping_guide(products)['comparison_takeaway']
        self.assertIn('#1 slim fit', takeaway)
        self.assertIn('#2 loose fit', takeaway)
        self.assertIn('#3 regular fit', takeaway)
        self.assertIn('listings describe', takeaway)
        self.assertIn('size chart', takeaway)
        self.assertNotIn('will fit', takeaway)

    def test_ambiguous_fit_evidence_does_not_create_contrast(self):
        products = [
            {'rank': 1, 'parent_asin': 'A', 'title': 'Everyday tee slim or loose fit', 'price': None},
            {'rank': 2, 'parent_asin': 'B', 'title': 'Everyday tee loose fit', 'price': None},
            {'rank': 3, 'parent_asin': 'C', 'title': 'Everyday tee regular fit', 'price': None},
        ]
        self.assertEqual(build_shopping_guide(products)['comparison_takeaway'], '')

    def test_multi_size_listing_does_not_pretend_to_be_one_size(self):
        products = [
            {'rank': 1, 'parent_asin': 'A', 'title': 'Black tee Small or Medium', 'price': None},
            {'rank': 2, 'parent_asin': 'B', 'title': 'Black tee Large', 'price': None},
            {'rank': 3, 'parent_asin': 'C', 'title': 'Black tee Medium', 'price': None},
        ]
        guide = build_shopping_guide(products)
        self.assertEqual(guide['comparison_takeaway'], '')
        self.assertNotIn('Listed size', guide['top_three'][0]['detail'])
