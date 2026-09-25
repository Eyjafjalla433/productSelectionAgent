"""Exercise the actual shared description pipeline through workflow boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from agentic_workflow.runtime import create_runtime
from mvp.audit import verify_audit
from mvp.server import AgentRuntime


class ComparisonProvider:
    name, model, base_url = 'fixture', 'description-fixture', 'http://localhost:1234/v1'

    def __init__(self, fail_stage=None):
        self.calls = []
        self.fail_stage = fail_stage

    def complete_json(self, **kwargs):
        payload = json.loads(kwargs['user'])
        stage = payload.get('stage')
        if not stage:  # Existing requirement assistance uses the same provider.
            return SimpleNamespace(data={'updates': []}, usage={'prompt_tokens': 0, 'completion_tokens': 0},
                                   provider=self.name, model=self.model, latency_ms=0)
        self.calls.append(payload)
        if stage == self.fail_stage:
            raise TimeoutError('simulated unavailable model')
        rows = []
        for product in payload['products']:
            asin = product['parent_asin']
            quote = product['sources']['product_description.0']
            ref = {'parent_asin': asin, 'source_field': 'product_description.0', 'quote': quote}
            if stage == 'extract_attributes':
                rows.append({'parent_asin': asin, 'attributes': {
                    'material': {'value': 'cotton', 'source_field': ref['source_field'],
                                 'evidence': quote, 'source_type': 'explicit'}}})
            elif stage == 'objective_comparison':
                rows.append({'parent_asin': asin, 'pros': [{'text': 'Cotton fabric', 'evidence_refs': [ref]}],
                             'cons': [{'text': 'Invented claim', 'evidence_refs': [{**ref, 'quote': 'waterproof'}]}]})
            else:
                rows.append({'parent_asin': asin, 'fit_reasons': [
                    {'text': 'Cotton is an option for your fabric preference.', 'evidence_refs': [ref]}]})
        return SimpleNamespace(data={'products': rows}, usage={'prompt_tokens': 10, 'completion_tokens': 5})


class DescriptionIntegrationTests(unittest.TestCase):
    def runtime(self, provider=None):
        def search(query, top_k):
            return [{'product_id': 'B', 'score': 2}, {'product_id': 'A', 'score': 1}][:top_k]

        def details(ids):
            return [{'product_id': asin, 'found': True, 'title': 'Blue cotton dress',
                     'brand': 'Example', 'color': 'blue', 'bullet_point': 'Machine washable',
                     'product_description': ['A blue dress made from cotton.'],
                     'details': {'Care': 'Cold wash'}} for asin in ids]

        return create_runtime(provider=provider, search_function=search, details_function=details)

    def test_offline_search_handoff_preserves_sources_and_selection_order(self):
        runtime = self.runtime()
        sid = runtime.new_session()['session_id']
        first = runtime.chat(sid, 'I need a blue dress')
        reply = runtime.chat(sid, 'Compare #2 and #1')
        handoff = reply['handoff']
        result = handoff['comparison_assist']
        self.assertEqual(result['schema_version'], 'description-comparison.v1')
        self.assertEqual(result['status'], 'offline_preview')
        self.assertEqual(result['selected_asins'], ['A', 'B'])
        self.assertEqual(reply['products'], first['products'])
        self.assertEqual(handoff['selected_products'][0]['product_description'], ['A blue dress made from cotton.'])
        self.assertEqual(handoff['selected_products'][0]['product_bullet_points'], ['Machine washable'])
        self.assertEqual(handoff['selected_products'][0]['details']['Care'], 'Cold wash')
        self.assertIsNone(result['product_profiles'][0]['attributes']['price']['value'])
        self.assertFalse(result['personalized_comparison']['personalization_applied'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_model_stages_usage_evidence_and_cached_export(self):
        provider = ComparisonProvider()
        runtime = self.runtime(provider)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress')
        self.assertEqual(provider.calls, [])
        reply = runtime.chat(sid, 'Compare #2 and #1')
        result = reply['handoff']['comparison_assist']
        self.assertEqual([call['stage'] for call in provider.calls],
                         ['extract_attributes', 'objective_comparison', 'personalized_comparison'])
        for call in provider.calls[:2]:
            self.assertNotIn('requirements', call)
            self.assertNotIn('ranking_score', json.dumps(call))
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['selected_asins'], ['A', 'B'])
        self.assertTrue(result['personalized_comparison']['personalization_applied'])
        self.assertTrue(result['products'][0]['pros'])
        self.assertEqual(result['products'][0]['cons'], [])
        self.assertEqual(reply['assistant']['usage'], {'prompt_tokens': 30, 'completion_tokens': 15})
        exported = runtime.selection_handoff(sid)['comparison_assist']
        self.assertTrue(exported['cached'])
        self.assertEqual(exported['usage_this_call'], {'prompt_tokens': 0, 'completion_tokens': 0})
        finalized = runtime.chat(sid, 'Finalize my selection')
        self.assertTrue(finalized['handoff']['comparison_assist']['cached'])
        self.assertEqual(len(provider.calls), 3)
        self.assertFalse(runtime.model_cloud)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_changed_source_or_requirements_refreshes_comparison(self):
        provider = ComparisonProvider()
        runtime = self.runtime(provider)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a dress')
        runtime.chat(sid, 'Compare #1')
        runtime.agent.retriever.products['B']['description'] = ['Updated cotton dress description.']
        refreshed = runtime.selection_handoff(sid)['comparison_assist']
        self.assertFalse(refreshed['cached'])
        self.assertEqual(len(provider.calls), 6)
        runtime.chat(sid, 'I prefer cotton')
        runtime.selection_handoff(sid)
        self.assertEqual(len(provider.calls), 9)
        self.assertIn('cotton', provider.calls[-1]['requirements']['soft']['material'])

    def test_model_failure_keeps_facts_and_finalization_available(self):
        runtime = self.runtime(ComparisonProvider(fail_stage='objective_comparison'))
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a dress')
        reply = runtime.chat(sid, 'Compare #1')
        result = reply['handoff']['comparison_assist']
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['product_profiles'][0]['attributes']['color']['value'], 'blue')
        self.assertEqual(result['usage'], {'prompt_tokens': 20, 'completion_tokens': 10})
        final = runtime.chat(sid, 'Finalize my selection')
        self.assertEqual(final['handoff']['status'], 'finalized')
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_demo_factory_uses_module_and_empty_shortlist_does_not_call_model(self):
        catalog = Path(__file__).resolve().parents[1] / 'mvp' / 'demo_data' / 'catalog.jsonl'
        runtime = AgentRuntime.create(catalog)
        sid = runtime.new_session()['session_id']
        self.assertNotIn('comparison_assist', runtime.selection_handoff(sid))
        runtime.chat(sid, 'I need a dress')
        reply = runtime.chat(sid, 'Compare #1')
        self.assertEqual(reply['handoff']['comparison_assist']['schema_version'], 'description-comparison.v1')
        self.assertEqual(reply['handoff']['comparison_assist']['status'], 'offline_preview')


if __name__ == '__main__':
    unittest.main()
