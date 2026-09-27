import io
import json
import time
import unittest
from urllib.error import HTTPError
from optional_bedrock.provider import GatewayProvider, ModelProviderError
from optional_bedrock.__main__ import GatewayRuntime
from mvp.demo import DEMO_CATALOG


class ProviderTests(unittest.TestCase):
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
        p = self.provider({'message': {'content': '{"updates":[]}'}})
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
