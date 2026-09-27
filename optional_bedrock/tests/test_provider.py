import io
import json
import time
import unittest
from urllib.error import HTTPError
from optional_bedrock.provider import GatewayProvider, ModelProviderError
from optional_bedrock.__main__ import GatewayRuntime
from mvp.demo import DEMO_CATALOG
from mvp.server import AgentRuntime
from unittest.mock import patch
from shopping_agent.requirement_parser import PrimaryRequirementParser


class ProviderTests(unittest.TestCase):
    @patch('optional_bedrock.provider.build_opener')
    def test_embedded_defaults_and_environment_override(self, _opener):
        with patch.dict('os.environ', {}, clear=True):
            provider = GatewayProvider.from_environment()
        self.assertEqual(provider.base_url, 'https://api.softwaresystems.app')
        self.assertTrue(provider.api_key)
        with patch.dict('os.environ', {'LLM_GATEWAY_URL': 'https://example.test',
                                     'LLM_GATEWAY_API_KEY': 'test-override',
                                     'LLM_MODEL': 'test-model'}, clear=True):
            provider = GatewayProvider.from_environment()
        self.assertEqual(provider.api_key, 'test-override')
        self.assertEqual(provider.model, 'test-model')
        self.assertEqual(provider.base_url, 'https://example.test')

    def test_standard_runtime_gateway_budget_and_disclosure(self):
        provider = self.runtime_provider()
        runtime = AgentRuntime.create(DEMO_CATALOG, provider=provider)
        original = provider.complete_json
        def completion(**kwargs):
            self.assertIsNotNone(provider.deadline)
            return original(**kwargs)
        provider.complete_json = completion
        sid = runtime.new_session()['session_id']
        runtime.chat(sid, 'I need a blue dress')
        runtime.chat(sid, 'Compare #1 and #2')
        self.assertIsNone(provider.deadline)
        self.assertEqual(runtime.health()['data_boundary'], 'aws_bedrock_gateway')

    def runtime_provider(self, **options):
        self.requests = []
        def opener(req, timeout):
            self.requests.append((req, timeout))
            body = json.loads(req.data)
            system = body['messages'][0]['content']
            user = json.loads(body['messages'][1]['content'].split('\nINPUT DATA\n', 1)[1])
            if system.startswith('Parse shopping requirements'):
                data = {'updates': [dict(slot=slot, operation='set', values=[value],
                                         constraint_type='hard', evidence=value)
                                    for slot, value in [('category', 'dress'), ('color', 'blue')]]}
            elif 'authoritative_response' in user:
                data = {'reply': user['authoritative_response'][:220], 'products': [
                    {'parent_asin': p['parent_asin'], 'why': 'Check the listed fabric and size.',
                     'evidence_ids': [r['id'] for r in p['evidence_candidates'][:1]]}
                    for p in user['products']]}
            else:
                data = {}  # Description pipeline exercises its existing explicit fallback.
            return io.BytesIO(json.dumps({'message': {'content': json.dumps(data)},
                                         'prompt_eval_count': 12, 'eval_count': 4}).encode())
        return GatewayProvider(base_url='https://example.test', api_key='test-only',
                               model='sonnet4.5', opener=opener, **options)

    def test_both_entry_points_share_full_parser_writer_and_provider(self):
        for runtime_class in (AgentRuntime, GatewayRuntime):
            with self.subTest(runtime=runtime_class.__name__):
                provider = self.runtime_provider()
                runtime = runtime_class.create(DEMO_CATALOG, provider=provider)
                self.assertIsInstance(runtime.agent.requirement_enhancer, PrimaryRequirementParser)
                self.assertIs(runtime.agent.requirement_enhancer.provider, provider)
                self.assertIs(runtime.response_writer.provider, provider)
                self.assertIs(runtime.comparison_enhancer.provider, provider)
                sid = runtime.new_session()['session_id']
                reply = runtime.chat(sid, 'I need a blue dress')
                self.assertEqual(reply['receipt']['hard'], {'category': 'dress', 'color': 'blue'})
                self.assertTrue(reply['receipt']['model_assist']['replaces_rules'])
                self.assertEqual(reply['receipt']['response_assist']['status'], 'applied')
                self.assertEqual(reply['receipt']['response_assist']['provider'], provider.name)
                self.assertTrue(reply['products'])
                self.assertTrue(all(p['advice']['fit_reason_source'] == provider.name for p in reply['products']))
                self.assertEqual(provider.calls, 2)
                self.assertEqual(len(self.requests), 2)
                self.assertEqual(reply['assistant']['usage'], {'prompt_tokens': 24, 'completion_tokens': 8})
                self.assertTrue(all(json.loads(req.data)['options']['num_predict'] == 1024 for req, _ in self.requests))
                self.assertIsNone(provider.deadline)

    def test_writer_cannot_bypass_call_limit(self):
        provider = self.runtime_provider(max_calls=1)
        runtime = AgentRuntime.create(DEMO_CATALOG, provider=provider)
        reply = runtime.chat(runtime.new_session()['session_id'], 'I need a blue dress')
        self.assertTrue(reply['receipt']['model_assist']['replaces_rules'])
        self.assertEqual(reply['receipt']['response_assist']['status'], 'fallback')
        self.assertTrue(reply['products'])
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(all('fit_reason_source' not in p['advice'] for p in reply['products']))

    def test_writer_cannot_reset_expired_turn_budget(self):
        provider = self.runtime_provider()
        original = provider.opener
        def expired_after_parser(req, timeout):
            response = original(req, timeout)
            provider.deadline = time.monotonic() - 1
            return response
        provider.opener = expired_after_parser
        runtime = AgentRuntime.create(DEMO_CATALOG, provider=provider)
        reply = runtime.chat(runtime.new_session()['session_id'], 'I need a blue dress')
        self.assertEqual(reply['receipt']['response_assist']['status'], 'fallback')
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(provider.deadline)

    def provider(self, envelope=None, **options):
        self.requests = []
        def opener(req, timeout):
            self.requests.append((req, timeout))
            return io.BytesIO(json.dumps(envelope if envelope is not None else {
                'message': {'content': '{"ok":true}'}, 'done': True,
                'prompt_eval_count': 12, 'eval_count': 4}).encode())
        return GatewayProvider(base_url='https://example.test', api_key='test-only',
                               model='sonnet4.5', opener=opener, **options)

    def test_contract(self):
        p = self.provider()
        r = p.complete_json(system='Extract facts', user='input', max_tokens=100)
        req, timeout = self.requests[0]
        payload = json.loads(req.data)
        self.assertEqual(req.full_url, 'https://example.test/api/chat')
        self.assertEqual(req.get_header('X-api-key'), 'test-only')
        self.assertFalse(payload['stream'])
        self.assertEqual(payload['options']['num_predict'], 100)
        self.assertIn('Extract facts', payload['messages'][1]['content'])
        self.assertEqual(r.usage, {'prompt_tokens': 12, 'completion_tokens': 4})
        self.assertTrue(r.data['ok'])

    def test_fence(self):
        p = self.provider({'message': {'content': '```json\n{"ok":true}\n```'}})
        self.assertTrue(p.complete_json(system='', user='').data['ok'])

    def test_output_limit_caps_large_tasks_and_preserves_small_requests(self):
        for requested, expected in [(64, 64), (700, 700), (1024, 1024), (1400, 1024), (2400, 1024), (4096, 1024)]:
            with self.subTest(requested=requested):
                provider = self.provider()
                provider.complete_json(system='', user='', max_tokens=requested)
                self.assertEqual(json.loads(self.requests[0][0].data)['options']['num_predict'], expected)

    def test_concatenated_objects_are_rejected_without_salvaging_first_object(self):
        for content in ['{"updates":[]} {"updates":[{}]}',
                        '```json\n{"updates":[]}\n``````json\n{"updates":[{}]}\n```']:
            with self.subTest(content=content), self.assertRaises(ModelProviderError):
                self.provider({'message': {'content': content}}).complete_json(system='', user='')

    def test_reject_invalid_outputs(self):
        for env in [{'message': {'content': '[]'}}, {'message': {'content': '{"x":NaN}'}},
                    {'message': {'content': 'not json'}}, {'error': 'secret'},
                    {'message': {'content': '{}', 'tool_calls': [{}]}},
                    {'message': {'content': '{}'}, 'done_reason': 'length'},
                    {'message': {'content': '{}'}, 'done': False},
                    {'message': {'content': '{}'}, 'eval_count': -1}]:
            with self.subTest(env=env), self.assertRaises(ModelProviderError):
                self.provider(env).complete_json(system='', user='')

    def test_limits(self):
        p = self.provider(max_calls=1)
        p.complete_json(system='', user='')
        with self.assertRaises(ModelProviderError):
            p.complete_json(system='', user='')
        self.assertEqual(len(self.requests), 1)
        p = self.provider(max_request_bytes=1024)
        with self.assertRaises(ModelProviderError):
            p.complete_json(system='', user='x'*2000)
        self.assertEqual(p.calls, 0)

    def test_operation_budget_and_nested_context(self):
        p = self.provider()
        with p.operation():
            deadline = p.deadline
            with p.operation():
                self.assertEqual(p.deadline, deadline)
            p.deadline = time.monotonic() - 1
            with self.assertRaises(ModelProviderError):
                p.complete_json(system='', user='')
        self.assertIsNone(p.deadline)
        self.assertEqual(p.calls, 0)

    def test_failure_does_not_leak_or_retry(self):
        p = self.provider()
        def failed(req, timeout):
            raise HTTPError(req.full_url, 401, 'test-only-secret', {}, io.BytesIO(b'secret'))
        p.opener = failed
        with self.assertRaisesRegex(ModelProviderError, '^Gateway HTTP 401$'):
            p.complete_json(system='', user='')
        self.assertEqual(p.calls, 1)

    def test_url_validation(self):
        for url in ['http://example.test', 'https://user:secret@example.test',
                    'https://example.test/api/chat', 'https://example.test/v1',
                    'https://example.test?key=secret']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                GatewayProvider(base_url=url, api_key='x', model='x')

    def test_integration_and_cloud_disclosure(self):
        p = self.runtime_provider()
        runtime = GatewayRuntime.create(DEMO_CATALOG, provider=p)
        sid = runtime.new_session()['session_id']
        reply = runtime.chat(sid, 'I need a blue dress')
        self.assertTrue(reply['products'])
        self.assertGreater(p.calls, 0)
        self.assertIsNone(p.deadline)
        self.assertTrue(runtime.health()['cloud_model'])
        self.assertEqual(runtime.health()['model_provider'], 'aws_bedrock_gateway')
        comparison = runtime.chat(sid, 'Compare #1 and #2')
        self.assertIn('comparison_assist', comparison['handoff'])
        final = runtime.chat(sid, 'Finalize my selection')
        self.assertTrue(final['selection_state']['finalized'])


if __name__ == '__main__':
    unittest.main()
