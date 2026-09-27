"""Probe both configured clouds once and choose a provider for this process."""
from concurrent.futures import ThreadPoolExecutor
import math

from .model_provider import create_model_provider


def select_startup_provider(*, timeout_seconds=20.0, factory=None, report=print):
    if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 120:
        raise ValueError('model timeout must be between 0 and 120 seconds')
    factory = factory or create_model_provider

    def probe(mode):
        try:
            provider = factory(mode, timeout_seconds=timeout_seconds)
            result = provider.complete_json(
                system='Return exactly the JSON object {"ok":true}.',
                user='Startup connectivity check. No shopper data.', max_tokens=64)
            if result.data.get('ok') is not True:
                return None, 'invalid health response'
            return provider, f'ready ({result.latency_ms:.0f} ms)'
        except Exception as exc:
            # Never log exception text: configuration/transport errors can contain secrets.
            return None, f'unavailable ({type(exc).__name__})'

    report('Checking gateway and DeepSeek (one small billable request each).')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(probe, ('gateway', 'deepseek')))
    for mode, (_, status) in zip(('gateway', 'deepseek'), results):
        report(f'Model startup: {mode}: {status}')
    selected = next((provider for provider, _ in results if provider is not None), None)
    report(f'Model selected: {selected.name}' if selected else
           'Both cloud providers unavailable; using model-free fallback.')
    return selected
