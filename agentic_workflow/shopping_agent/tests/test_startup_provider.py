import unittest
from unittest.mock import Mock

from shopping_agent.model_provider import ModelProviderError, create_model_provider
from shopping_agent.startup_provider import select_startup_provider


class StartupProviderTests(unittest.TestCase):
    def select(self, gateway=True, deepseek=True):
        providers = {}
        for mode, healthy in [('gateway', gateway), ('deepseek', deepseek)]:
            provider = Mock()
            provider.name = mode
            if healthy is None:
                provider.complete_json.return_value.data = {'ok': False}
            elif healthy:
                provider.complete_json.return_value.data = {'ok': True}
            else:
                provider.complete_json.side_effect = ModelProviderError('secret-key')
            provider.complete_json.return_value.latency_ms = 1
            providers[mode] = provider
        logs = []
        result = select_startup_provider(factory=lambda mode, **kw: providers[mode], report=logs.append)
        for provider in providers.values():
            provider.complete_json.assert_called_once()
        self.assertNotIn('secret-key', '\n'.join(logs))
        return result, providers

    def test_gateway_priority_and_both_probed(self):
        result, providers = self.select()
        self.assertIs(result, providers['gateway'])

    def test_each_single_available_provider(self):
        result, providers = self.select(gateway=False)
        self.assertIs(result, providers['deepseek'])
        result, providers = self.select(deepseek=False)
        self.assertIs(result, providers['gateway'])

    def test_both_down(self):
        self.assertIsNone(self.select(False, False)[0])

    def test_bad_json_health_is_not_ready(self):
        result, providers = self.select(gateway=None)
        self.assertIs(result, providers['deepseek'])

    def test_bad_gateway_config_does_not_block_deepseek(self):
        provider = Mock()
        provider.name = 'deepseek'
        provider.complete_json.return_value.data = {'ok': True}
        provider.complete_json.return_value.latency_ms = 1
        def factory(mode, **kwargs):
            if mode == 'gateway':
                raise ValueError('secret-key')
            return provider
        logs = []
        self.assertIs(select_startup_provider(factory=factory, report=logs.append), provider)
        self.assertNotIn('secret-key', '\n'.join(logs))

    def test_explicit_off(self):
        self.assertIsNone(create_model_provider('off'))

    def test_auto_rejects_ambiguous_overrides(self):
        with self.assertRaises(ValueError):
            create_model_provider('auto', base_url='https://example.test')

    def test_invalid_timeout(self):
        with self.assertRaises(ValueError):
            select_startup_provider(timeout_seconds=float('nan'))
