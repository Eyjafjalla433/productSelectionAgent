import unittest

from mvp.shopping_guide import build_shopping_guide, describe


class ShoppingGuideTests(unittest.TestCase):
    def product(self, rank=1, **values):
        return dict(rank=rank, parent_asin=f'P{rank}', title='Blue dress', **values)

    def test_top_ten_preserves_rank_and_separates_three(self):
        products = [self.product(i) for i in range(1, 13)]
        guide = build_shopping_guide(products)
        self.assertEqual([p['rank'] for p in guide['top_three']], [1, 2, 3])
        self.assertEqual([p['rank'] for p in guide['other_options']], list(range(4, 11)))
        self.assertEqual(len(products), 12)

    def test_features_require_source_evidence(self):
        product = self.product(features=['Cotton blend with pockets'])
        notes = describe(product)
        self.assertIn('Pockets', notes['feature'])
        self.assertEqual(notes['caution'], '')
        self.assertIn('Contains cotton', notes['detail'])
        self.assertTrue(all(e['quote'] in product['features'] for e in notes['evidence']))

    def test_missing_data_is_not_invented(self):
        notes = describe(self.product())
        self.assertEqual(notes['evidence'], [])
        self.assertIsNone(notes['price'])
        self.assertEqual(notes['caution'], '')
        self.assertEqual(notes['feature'], '')
        self.assertEqual(notes['detail'], '')
        self.assertNotIn('棉质', notes['feature'])

    def test_empty_results(self):
        guide = build_shopping_guide([])
        self.assertEqual(guide['count'], 0)
        self.assertEqual(guide['intro'], '')

    def test_reported_products_have_distinct_notes(self):
        titles = ['Impact Radiohead Bear Soft Fitted 30/1 Cotton Tee (Medium) Black',
                  'Impact Radiohead Backwards Soft Fitted 30/1 Cotton Tee (Small) Black',
                  'WWE Authentic Wear NWO Wolfpac Wolf T-Shirt Black Large']
        rows = [describe(dict(rank=i+1, parent_asin=str(i), title=t)) for i, t in enumerate(titles)]
        self.assertEqual(len({r['feature'] for r in rows}), 3)
        for row, size in zip(rows, ('M', 'S', 'L')):
            self.assertIn('Listed size ' + size, row['detail'])
            self.assertLess(len(row['feature']), 45)
            self.assertNotIn('提到', row['feature'] + row['detail'])

    def test_missing_price_is_one_shared_note(self):
        guide = build_shopping_guide([self.product(i) for i in range(1, 4)])
        self.assertIn('Prices are unavailable', guide['data_note'])
        self.assertTrue(all(not row['caution'] for row in guide['top_three']))
