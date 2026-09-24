"""Launch the conversational web workflow with search_tool."""
import argparse
from .runtime import create_runtime
from mvp.server import AgentRuntime, create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--demo', action='store_true', help='explicit synthetic offline catalog')
    args = parser.parse_args()
    if args.demo:
        from mvp.demo import DEMO_CATALOG, DEMO_WEB_SCENARIOS
        runtime = AgentRuntime.create(DEMO_CATALOG, scenarios=DEMO_WEB_SCENARIOS)
    else:
        from .preflight import check_search_tool
        problems = check_search_tool()
        if problems:
            parser.error('\n'.join(problems))
        runtime = create_runtime()
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
