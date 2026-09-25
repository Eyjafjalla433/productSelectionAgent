import copy
import json
from types import SimpleNamespace
import unittest
from description_module import DescriptionComparison


def handoff():
    return {'schema_version': 'show-me-your-agent.selection.v1', 'requirements': {
        'hard': {'category': 'tshirt'}, 'soft': {'comfort': ['high']}, 'excluded': {}},
        'selected_products': [{'parent_asin': 'A', 'title': 'Cotton T-shirt',
            'product_description': [], 'product_bullet_points': ['Soft cotton fabric'],
            'details': {'Color': 'Black'}, 'ranking_score': 3.4,
            'advice': {'secret': 'MUST NOT ENTER OBJECTIVE'}}]}


class Provider:
    name, model = 'test', 'fixture'
    def __init__(self):
        self.calls = []
    def complete_json(self, **kwargs):
        payload = json.loads(kwargs['user'])
        self.calls.append(payload)
        stage = payload['stage']
        ref = {'parent_asin': 'A', 'source_field': 'product_bullet_points.0', 'quote': 'Soft cotton fabric'}
        if stage == 'extract_attributes':
            data = {'products': [{'parent_asin': 'A', 'category': 'laptop', 'attributes': {
                'material': {'value': 'cotton', 'source_field': ref['source_field'], 'evidence': ref['quote'], 'source_type': 'explicit'},
                'waterproof': {'value': True, 'source_field': 'title', 'evidence': 'waterproof', 'source_type': 'explicit'}}}]}
        elif stage == 'objective_comparison':
            data = {'products': [{'parent_asin': 'A', 'pros': [{'text': 'Cotton fabric', 'evidence_refs': [ref]}],
                'cons': [{'text': 'Bad claim', 'evidence_refs': [{**ref, 'quote': 'waterproof'}]}]}]}
        else:
            data = {'products': [{'parent_asin': 'A', 'fit_reasons': [{'text': 'Soft fabric may suit comfort preference', 'evidence_refs': [ref]}]}]}
        return SimpleNamespace(data=data, usage={'prompt_tokens': 10, 'completion_tokens': 5})


class Tests(unittest.TestCase):
    def test_three_stages_separation_evidence_and_compatibility(self):
        p = Provider()
        original = handoff()
        before = copy.deepcopy(original)
        result = DescriptionComparison(p).compare(original).to_dict()
        self.assertEqual(original, before)
        self.assertEqual(len(p.calls), 3)
        for payload in p.calls[:2]:
            self.assertNotIn('requirements', payload)
            self.assertNotIn('comfort', json.dumps(payload))
            self.assertNotIn('MUST NOT', json.dumps(payload))
            self.assertNotIn('ranking_score', json.dumps(payload))
        self.assertEqual(result['product_profiles'][0]['category'], 'tshirt')
        self.assertNotIn('waterproof', result['product_profiles'][0]['attributes'])
        self.assertEqual(result['products'][0]['cons'], [])
        self.assertEqual(result['products'][0]['pros'][0]['evidence'], 'Soft cotton fabric')
        self.assertEqual(result['usage']['prompt_tokens'], 30)
        self.assertFalse(result['personalized_comparison']['ranking_changed'])

    def test_offline_explicit_unknowns(self):
        result = DescriptionComparison().compare(handoff()).to_dict()
        self.assertEqual(result['status'], 'offline_preview')
        self.assertIsNone(result['product_profiles'][0]['attributes']['price']['value'])
        self.assertFalse(result['personalized_comparison']['personalization_applied'])

    def test_no_preferences_skips_personalization(self):
        h, p = handoff(), Provider()
        h['requirements'] = {}
        result = DescriptionComparison(p).compare(h).to_dict()
        self.assertEqual(len(p.calls), 2)
        self.assertFalse(result['personalized_comparison']['personalization_applied'])

    def test_empty_selection_and_limit(self):
        h = handoff()
        h['selected_products'] = []
        self.assertEqual(DescriptionComparison().compare(h).to_dict()['status'], 'empty')
        h['selected_products'] = handoff()['selected_products'] * 4
        with self.assertRaises(ValueError):
            DescriptionComparison().compare(h)

    def test_duplicate_id(self):
        h = handoff()
        h['selected_products'] *= 2
        with self.assertRaises(ValueError):
            DescriptionComparison().compare(h)

    def test_provider_failure_preserves_contract(self):
        class Broken(Provider):
            def complete_json(self, **kwargs):
                raise TimeoutError()
        result = DescriptionComparison(Broken()).compare(handoff()).to_dict()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['selected_asins'], ['A'])
        self.assertEqual(result['products'][0]['pros'], [])

    def test_objective_unchanged_when_preferences_change(self):
        a, b = handoff(), handoff()
        b['requirements']['soft'] = {'color': ['blue']}
        first = DescriptionComparison(Provider()).compare(a).to_dict()
        second = DescriptionComparison(Provider()).compare(b).to_dict()
        self.assertEqual(first['objective_comparison'], second['objective_comparison'])

    def test_unknown_category_is_generic(self):
        h = handoff()
        h['requirements']['hard']['category'] = 'unrecognized'
        self.assertEqual(DescriptionComparison().compare(h).to_dict()['product_profiles'][0]['category'], 'generic')


if __name__ == '__main__':
    unittest.main()
