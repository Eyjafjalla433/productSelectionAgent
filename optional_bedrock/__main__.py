"""Run with python -B -m optional_bedrock; delete this package to remove integration."""
import argparse
import json
from pathlib import Path
from .provider import GatewayProvider, load_environment
from shopping_agent.model_provider import ModelProviderError
from mvp.server import AgentRuntime, create_server


class GatewayRuntime(AgentRuntime):
    """Compatibility entry point; the shared runtime handles gateway budgets and disclosure."""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Synthetic catalog, real gateway model')
    parser.add_argument('--check', action='store_true', help='Make ONE real, billable JSON connectivity call')
    parser.add_argument('--warmup', action='store_true', help='Load and query local search before accepting requests')
    parser.add_argument('--env-file', default=str(Path(__file__).with_name('.env')))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8001)
    args = parser.parse_args()
    try:
        load_environment(args.env_file)
        if args.check:
            provider = GatewayProvider.from_environment()
            result = provider.complete_json(system='Return a JSON object with ok set to true.',
                                            user='Connectivity test. No product or personal data.', max_tokens=64)
            if result.data.get('ok') is not True:
                raise ModelProviderError('Gateway did not return the expected JSON object')
            print(json.dumps({'ok': True, 'provider': provider.name, 'model': provider.model,
                              'usage': result.usage, 'latency_ms': result.latency_ms}))
            return
        from shopping_agent.startup_provider import select_startup_provider
        provider = select_startup_provider()
        runtime_class = GatewayRuntime if getattr(provider, 'name', None) == 'aws_bedrock_gateway' else AgentRuntime
        if args.demo:
            from mvp.demo import DEMO_CATALOG, DEMO_WEB_SCENARIOS
            runtime = runtime_class.create(DEMO_CATALOG, provider=provider, scenarios=DEMO_WEB_SCENARIOS)
        else:
            from agentic_workflow.preflight import check_search_tool
            from agentic_workflow.runtime import SEARCH_SCENARIOS
            errors = check_search_tool()
            if errors:
                raise ValueError('; '.join(errors))
            runtime = runtime_class.create(None, provider=provider, scenarios=SEARCH_SCENARIOS)
            if args.warmup:
                print('Warming local search; no cloud call is made for warmup.', flush=True)
                search, _ = runtime.agent.retriever._functions()
                search('blue dress', 10)
        server = create_server(runtime, args.host, args.port)
    except (ValueError, ModelProviderError) as exc:
        parser.error(str(exc))
    print(f'Optional Bedrock workflow: http://{args.host}:{args.port}', flush=True)
    if hasattr(provider, 'max_calls'):
        print(f'Gateway limit: {provider.max_calls} attempts per process (including startup probe).', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
