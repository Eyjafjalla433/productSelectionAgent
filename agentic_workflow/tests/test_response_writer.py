import json
import unittest
from copy import deepcopy

import agentic_workflow
from shopping_agent.response_writer import ResponseWriter, evidence_candidates
from shopping_agent.tests.test_model_provider import FakeProvider
from agentic_workflow.tests import test_material_reversal
from mvp.audit import verify_audit


class ResponseWriterTests(unittest.TestCase):
    def fixture(self):
        match = {'signals': [{'tier': 'hard', 'slot': 'material', 'value': 'linen', 'status': 'supported'}]}
        catalog = {'title': 'Blue shirt', 'description': 'Nice packaging. Made of linen. ' + 'Marketing. ' * 100}
        return dict(message='linen please', assistant={'message': 'Original reply', 'usage': {}},
                    products=[{'parent_asin': 'a', 'rank': 1, 'title': 'Blue shirt', 'match': match,
                               'advice': {'cons': [{'text': 'Unknown size'}]}}],
                    receipt={'hard': {'material': 'linen'}}, catalogs={'a': catalog}, history=[], selection={})

    def test_relevant_exact_short_quotes(self):
        args = self.fixture()
        rows = evidence_candidates(args['catalogs']['a'], args['products'][0]['match'])
        self.assertEqual(rows[0]['evidence'], 'Made of linen.')
        self.assertTrue(all(len(r['evidence']) <= 160 for r in rows))
        self.assertFalse(any('Marketing' in r['evidence'] for r in rows))

    def test_source_tracks_actual_completion_provider(self):
        for name in ('deepseek', 'aws_bedrock_gateway'):
            with self.subTest(provider=name):
                args = self.fixture()
                provider = FakeProvider({'reply': 'Here is a linen option.', 'products': [
                    {'parent_asin': 'a', 'why': 'The fabric is linen.', 'evidence_ids': ['e0']}]})
                provider.name = name
                out = ResponseWriter(provider).write(**args)
                self.assertEqual(out['status'], 'applied')
                self.assertEqual(out['provider'], name)
                self.assertEqual(args['products'][0]['advice']['fit_reason_source'], name)
                provider.error = True
                out = ResponseWriter(provider).write(**args)
                self.assertEqual(out['status'], 'fallback')
                self.assertNotIn('fit_reason_source', args['products'][0]['advice'])

    def test_valid_copy_preserves_facts_and_selects_only_source_quotes(self):
        args = self.fixture()
        provider = FakeProvider({'reply': 'Linen it is—take a look at this one. Want more?', 'products': [
            {'parent_asin': 'a', 'why': 'The linen fabric matches your material choice.', 'evidence_ids': ['e0']}]})
        before = deepcopy(args['products'][0]['match'])
        outcome = ResponseWriter(provider).write(**args)
        self.assertEqual(outcome['status'], 'applied')
        self.assertEqual(args['assistant']['message'], 'Linen it is—take a look at this one.')
        self.assertEqual(args['products'][0]['match'], before)
        self.assertEqual(args['products'][0]['advice']['catalog_highlights'][0]['evidence'], 'Made of linen.')
        self.assertEqual(args['products'][0]['advice']['cons'], [{'text': 'Unknown size'}])

    def test_invalid_quote_product_or_long_reply_falls_back(self):
        for asin, evidence, reply in [('wrong', ['e0'], 'Hi'), ('a', ['invented'], 'Hi'), ('a', ['e0'], 'x' * 241), ('a', ['e0'], 'Only available in XL')]:
            args = self.fixture()
            provider = FakeProvider({'reply': reply, 'products': [
                {'parent_asin': asin, 'why': 'Reason', 'evidence_ids': evidence}]})
            out = ResponseWriter(provider).write(**args)
            self.assertEqual(out['status'], 'fallback')
            self.assertEqual(args['assistant']['message'], 'Original reply')
            self.assertNotIn('fit_reason', args['products'][0]['advice'])

    def test_failure_removes_stale_reason_and_preserves_pending_question(self):
        args = self.fixture()
        args['products'][0]['advice']['fit_reason'] = 'Old cotton preference'
        out = ResponseWriter(FakeProvider(error=True)).write(**args)
        self.assertEqual(out['status'], 'fallback')
        self.assertNotIn('fit_reason', args['products'][0]['advice'])
        args['receipt']['question'] = {'target_slot': 'size'}
        args['assistant']['message'] = 'What size do you need?'
        provider = FakeProvider({'reply': 'Choose this.', 'products': [
            {'parent_asin': 'a', 'why': 'Linen', 'evidence_ids': ['e0']}]})
        self.assertEqual(ResponseWriter(provider).write(**args)['status'], 'fallback')

    def test_runtime_context_usage_audit_and_control_turn(self):
        class WriterProvider(FakeProvider):
            def complete_json(self, **kwargs):
                self.payload = json.loads(kwargs['user'])
                self.data = {'reply': self.payload['required_question'] or 'Here are a few worth a look.',
                             'products': [{'parent_asin': p['parent_asin'], 'why': 'Check the listed fabric.',
                                           'evidence_ids': [r['id'] for r in p['evidence_candidates'][:1]]}
                                          for p in self.payload['products']]}
                return super().complete_json(**kwargs)
        provider = WriterProvider()
        runtime = test_material_reversal.MaterialReversalTests.runtime()
        runtime.response_writer = ResponseWriter(provider)
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, 'I want a blue cotton shirt')
        self.assertEqual(result['receipt']['response_assist']['status'], 'applied')
        self.assertEqual(provider.payload['profile']['hard']['material'], 'cotton')
        self.assertEqual(result['assistant']['usage']['prompt_tokens'], 12)
        self.assertIn('fit_reason', result['products'][0]['advice'])
        runtime.chat(sid, 'What are my preferences?')
        self.assertTrue(provider.payload['recent_conversation'])
        self.assertEqual(verify_audit(runtime.audit(sid)), [])
