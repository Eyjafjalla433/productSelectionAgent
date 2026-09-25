"""Exercise the actual shared description pipeline through workflow boundaries."""
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from agentic_workflow.runtime import create_runtime
from mvp.audit import verify_audit
from mvp.server import AgentRuntime
from shopping_agent.description_adapter import DescriptionComparisonAdapter


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
    def test_offline_labeled_facts_keep_evidence_without_inventing_composition(self):
        handoff = {
            'schema_version': 'show-me-your-agent.selection.v1',
            'requirements': {'hard': {'category': 'tshirt'}},
            'selected_products': [{'parent_asin': 'A', 'title': 'Example shirt',
                                   'details': {'Fabric Type': 'Cotton jersey',
                                               'Care': 'Cold wash', 'Size': 'M'}}],
        }
        original = deepcopy(handoff)
        result = DescriptionComparisonAdapter().compare(handoff).to_dict()
        attrs = result['product_profiles'][0]['attributes']
        self.assertEqual(attrs['material'], {
            'value': 'Cotton jersey', 'evidence': 'Cotton jersey',
            'source_field': 'details.Fabric Type', 'source_type': 'explicit'})
        self.assertEqual(attrs['care']['value'], 'Cold wash')
        self.assertEqual(attrs['size']['value'], 'M')
        self.assertIsNone(attrs['fabric_composition']['value'])
        self.assertIsNone(attrs['price']['value'])
        matrix = {r['dimension']: r['values']['A']
                  for r in result['objective_comparison']['comparison_matrix']}
        self.assertEqual(matrix['material'], attrs['material'])
        unknowns = result['objective_comparison']['unknowns'][0]['attributes']
        self.assertNotIn('material', unknowns)
        self.assertIn('price', unknowns)
        self.assertEqual(result['status'], 'offline_preview')
        self.assertEqual(result['usage'], {'prompt_tokens': 0, 'completion_tokens': 0})
        self.assertFalse(result['personalized_comparison']['personalization_applied'])
        self.assertEqual(handoff, original)

    def test_offline_conflicting_and_placeholder_metadata_remains_unknown(self):
        result = DescriptionComparisonAdapter().compare({
            'schema_version': 'show-me-your-agent.selection.v1',
            'selected_products': [{'parent_asin': 'A', 'title': 'Example shirt',
                                   'details': {'Material': 'Cotton', 'Fabric': 'Polyester',
                                               'Size': 'N/A', 'Care': 'Unknown',
                                               'Weight': float('nan')}}],
        }).to_dict()
        attrs = result['product_profiles'][0]['attributes']
        for field in ('material', 'size', 'care', 'weight'):
            self.assertIsNone(attrs[field]['value'])
        self.assertEqual(result['provenance']['workflow_direct_fields'], [])

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

    def test_explicit_retry_recovers_partial_comparison_without_search_or_reselection(self):
        for retry in ('Try again', 'Compare #2 and #1'):
            with self.subTest(retry=retry):
                provider = ComparisonProvider(fail_stage='objective_comparison')
                runtime = self.runtime(provider)
                sid = runtime.new_session()['session_id']
                runtime.chat(sid, 'I need a dress')
                partial = runtime.chat(sid, 'Compare #2 and #1')
                self.assertEqual(partial['receipt']['suggested_replies'], ['Try again'])
                self.assertIn("couldn't be completed", partial['assistant']['message'])
                runtime.selection_handoff(sid)
                self.assertEqual(len(provider.calls), 3)
                provider.fail_stage = None
                recovered = runtime.chat(sid, retry)
                assist = recovered['handoff']['comparison_assist']
                self.assertEqual(assist['status'], 'completed')
                self.assertFalse(assist['cached'])
                self.assertEqual(assist['selected_asins'], ['A', 'B'])
                self.assertEqual(len(provider.calls), 6)
                self.assertEqual(recovered['products'], partial['products'])
                self.assertEqual(recovered['selection_state']['selected_asins'],
                                 partial['selection_state']['selected_asins'])
                self.assertEqual(recovered['assistant']['usage'], {'prompt_tokens': 30, 'completion_tokens': 15})
                self.assertIsNone(recovered['receipt']['suggested_replies'])
                self.assertNotIn('2_retrieval', [event['stage'] for event in runtime.agent.get_trace(sid, 3)['events']])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_invalid_comparison_preserves_saved_items_and_does_not_call_model(self):
        provider = ComparisonProvider()
        runtime = self.runtime(provider)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a dress')
        saved = runtime.chat(sid, 'Keep #1')
        for request in ('Compare #99', 'Compare #2 and #99', 'Compare option 11', 'Compare #0'):
            with self.subTest(request=request):
                reply = runtime.chat(sid, request)
                self.assertIn("I can't find", reply['assistant']['message'])
                self.assertIsNone(reply.get('handoff'))
                self.assertEqual(reply['selection_state']['selected_asins'],
                                 saved['selection_state']['selected_asins'])
                self.assertEqual(reply['products'], saved['products'])
                self.assertEqual(provider.calls, [])
        recovered = runtime.chat(sid, '#1 and #2')
        self.assertEqual(recovered['handoff']['comparison_assist']['selected_asins'], ['B', 'A'])
        self.assertEqual(len(provider.calls), 3)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_explicit_comparison_uses_requested_items_even_with_full_shortlist(self):
        def search(query, top_k):
            return [{'product_id': str(i), 'score': 10-i} for i in range(5)]

        def details(ids):
            return [{'product_id': asin, 'found': True, 'title': 'Blue cotton dress',
                     'product_description': ['A cotton dress.']} for asin in ids]

        provider = ComparisonProvider()
        runtime = create_runtime(provider=provider, search_function=search, details_function=details)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a dress')
        saved = runtime.chat(sid, 'Keep #1, #2 and #3')['selection_state']['selected_asins']
        subset = runtime.chat(sid, 'Compare #3 and #2')
        self.assertEqual(subset['handoff']['comparison_assist']['selected_asins'], ['2', '1'])
        self.assertEqual(subset['selection_state']['selected_asins'], saved)
        ordinal = runtime.chat(sid, 'Compare the third and the second')
        self.assertEqual(ordinal['handoff']['comparison_assist']['selected_asins'], ['2', '1'])
        self.assertTrue(ordinal['handoff']['comparison_assist']['cached'])
        natural = runtime.chat(sid, "What's the difference between the third and the second?")
        self.assertEqual(natural['handoff']['comparison_assist']['selected_asins'], ['2', '1'])
        self.assertTrue(natural['handoff']['comparison_assist']['cached'])
        self.assertEqual(natural['products'], ordinal['products'])
        self.assertEqual(natural['selection_state']['selected_asins'], saved)
        extra = runtime.chat(sid, 'Compare #1 and #4')
        self.assertEqual(extra['handoff']['comparison_assist']['selected_asins'], ['0', '3'])
        self.assertEqual(extra['handoff']['saved_asins'], saved)
        self.assertEqual(extra['selection_state']['selected_asins'], saved)
        self.assertIn('without saving', extra['assistant']['message'])
        self.assertEqual(extra['handoff']['comparison_scope'], 'requested_products')
        exported = runtime.selection_handoff(sid)
        self.assertEqual(exported['comparison_scope'], 'saved_options')
        self.assertEqual(exported['comparison_assist']['selected_asins'], saved)
        count = len(provider.calls)
        oversized = runtime.chat(sid, 'Compare #1, #2, #3 and #4')
        self.assertIsNone(oversized.get('handoff'))
        self.assertEqual(oversized['selection_state']['selected_asins'], saved)
        self.assertEqual(len(provider.calls), count)
        resolved = runtime.chat(sid, '#2 and #4')
        self.assertEqual(resolved['handoff']['comparison_assist']['selected_asins'], ['1', '3'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_preference_and_comparison_share_one_turn_and_keep_original_references(self):
        for message in ('I prefer cotton, compare my saved options',
                        'Compare my saved options, I prefer cotton',
                        'I prefer cotton and compare #2 and #1',
                        'Compare #2 and #1, I prefer cotton',
                        "What's the difference between #2 and #1? I prefer cotton.",
                        "I prefer cotton, what's the difference between #2 and #1?",
                        'I prefer cotton, compare these two',
                        'Compare these two, I prefer cotton'):
            with self.subTest(message=message):
                searches = []

                def search(query, top_k):
                    searches.append(query)
                    ids = ['A', 'B'] if len(searches) == 1 else ['C', 'D']
                    return [{'product_id': asin, 'score': 2-i} for i, asin in enumerate(ids)]

                def details(ids):
                    return [{'product_id': asin, 'found': True, 'title': 'Blue cotton dress',
                             'product_description': ['A cotton dress.']} for asin in ids]

                provider = ComparisonProvider()
                runtime = create_runtime(provider=provider, search_function=search, details_function=details)
                sid = runtime.new_session()['session_id']
                runtime.chat(sid, 'I need a dress')
                runtime.chat(sid, 'Keep #1 and #2')
                reply = runtime.chat(sid, message)
                self.assertEqual(reply['turn'], 3)
                self.assertEqual(reply['receipt']['soft']['material'], ['cotton'])
                self.assertEqual([p['parent_asin'] for p in reply['products']], ['C', 'D'])
                expected = ['B', 'A'] if '#2' in message else ['A', 'B']
                self.assertEqual(reply['handoff']['comparison_assist']['selected_asins'], expected)
                self.assertEqual(provider.calls[-1]['requirements']['soft']['material'], ['cotton'])
                self.assertEqual(reply['receipt']['model_usage'], reply['assistant']['usage'])
                self.assertEqual(reply['assistant']['usage'], {'prompt_tokens': 30, 'completion_tokens': 15})
                self.assertEqual(runtime.audit(sid)['turns'][-1]['user_message'], message)
                undone = runtime.chat(sid, 'Undo')
                self.assertNotIn('material', undone['receipt']['soft'])
                self.assertEqual(undone['selection_state']['selected_asins'], ['A', 'B'])
                self.assertEqual(verify_audit(runtime.audit(sid)), [])

    def test_retry_after_combined_turn_keeps_original_ids_when_page_changes(self):
        searches = []

        def search(query, top_k):
            searches.append(query)
            ids = ['A', 'B'] if len(searches) == 1 else ['C', 'D']
            return [{'product_id': asin, 'score': 2-i} for i, asin in enumerate(ids)]

        def details(ids):
            return [{'product_id': asin, 'found': True, 'title': 'Blue cotton dress',
                     'product_description': ['A cotton dress.']} for asin in ids]

        provider = ComparisonProvider(fail_stage='objective_comparison')
        runtime = create_runtime(provider=provider, search_function=search, details_function=details)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a dress')
        runtime.chat(sid, 'Keep #1 and #2')
        partial = runtime.chat(sid, 'I prefer cotton, compare #2 and #1')
        self.assertEqual(partial['handoff']['comparison_assist']['status'], 'partial')
        still_partial = runtime.chat(sid, 'Try again')
        self.assertEqual(still_partial['handoff']['comparison_assist']['status'], 'partial')
        self.assertEqual(still_partial['handoff']['comparison_assist']['selected_asins'], ['B', 'A'])
        self.assertEqual(len(searches), 2)
        provider.fail_stage = None
        recovered = runtime.chat(sid, 'Try again')
        self.assertEqual(recovered['handoff']['comparison_assist']['status'], 'completed')
        self.assertEqual(recovered['handoff']['comparison_assist']['selected_asins'], ['B', 'A'])
        self.assertEqual(recovered['products'], partial['products'])
        self.assertEqual(len(searches), 2)
        self.assertEqual(recovered['selection_state']['selected_asins'], ['A', 'B'])
        self.assertEqual(recovered['receipt']['soft']['material'], ['cotton'])
        self.assertEqual(provider.calls[-1]['requirements']['soft']['material'], ['cotton'])
        self.assertEqual(len(provider.calls), 9)
        self.assertEqual(partial['receipt']['suggested_replies'], ['Try again'])
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

    def test_compare_saved_uses_saved_ids_after_results_and_preferences_change(self):
        searches = []

        def search(query, top_k):
            searches.append(query)
            ids = ['A', 'B'] if len(searches) == 1 else ['C', 'D']
            return [{'product_id': asin, 'score': 2-index} for index, asin in enumerate(ids)]

        def details(ids):
            return [{'product_id': asin, 'found': True, 'title': 'Blue cotton dress',
                     'product_description': ['A cotton dress.']} for asin in ids]

        provider = ComparisonProvider()
        runtime = create_runtime(provider=provider, search_function=search, details_function=details)
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a dress')
        runtime.chat(sid, 'Keep #1 and #2')
        revised = runtime.chat(sid, 'I prefer cotton')
        self.assertEqual([row['parent_asin'] for row in revised['products']], ['C', 'D'])
        search_count = len(searches)
        compared = runtime.chat(sid, 'Compare my saved options')
        self.assertIn('2 saved options side by side', compared['assistant']['message'])
        self.assertNotIn('No ranks', compared['assistant']['message'])
        self.assertEqual(compared['handoff']['comparison_assist']['selected_asins'], ['A', 'B'])
        self.assertEqual(compared['products'], revised['products'])
        self.assertEqual(len(searches), search_count)
        self.assertIn('cotton', provider.calls[-1]['requirements']['soft']['material'])
        provider_count = len(provider.calls)
        natural = runtime.chat(sid, 'How do my saved options differ?')
        self.assertEqual(natural['handoff']['comparison_assist']['selected_asins'], ['A', 'B'])
        self.assertEqual(natural['handoff']['comparison_scope'], 'saved_options')
        self.assertTrue(natural['handoff']['comparison_assist']['cached'])
        self.assertEqual(natural['products'], revised['products'])
        self.assertEqual(len(searches), search_count)
        self.assertEqual(len(provider.calls), provider_count)
        self.assertEqual(verify_audit(runtime.audit(sid)), [])


if __name__ == '__main__':
    unittest.main()
