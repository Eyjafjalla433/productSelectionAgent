"""Launch the conversational web workflow with search_tool."""
import argparse
from .runtime import create_runtime
from mvp.server import AgentRuntime, create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--demo', action='store_true', help='explicit synthetic offline catalog')
    parser.add_argument('--model-provider', choices=('off', 'local', 'deepseek'), default='off')
    parser.add_argument('--model', help='model name for requirement and description assistance')
    parser.add_argument('--model-base-url', help='override the configured model endpoint')
    parser.add_argument('--model-timeout', type=float, default=20.0)
    args = parser.parse_args()
    from shopping_agent.model_provider import create_model_provider
    try:
        provider = create_model_provider(args.model_provider, model=args.model,
                                         base_url=args.model_base_url,
                                         timeout_seconds=args.model_timeout)
    except ValueError as exc:
        parser.error(str(exc))
    if args.demo:
        from mvp.demo import DEMO_CATALOG, DEMO_WEB_SCENARIOS
        runtime = AgentRuntime.create(DEMO_CATALOG, scenarios=DEMO_WEB_SCENARIOS, provider=provider)
    else:
        from .preflight import check_search_tool
        problems = check_search_tool()
        if problems:
            parser.error('\n'.join(problems))
        runtime = create_runtime(provider=provider)
    server = create_server(runtime, args.host, args.port)
    print(f'Workflow: http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
