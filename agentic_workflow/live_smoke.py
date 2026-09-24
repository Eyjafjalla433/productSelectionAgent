"""Check actual search_tool inference and the full selection lifecycle."""
import argparse
import hashlib
import json
from pathlib import Path

from .preflight import check_search_tool
from .runtime import create_runtime
from mvp.audit import verify_audit


def tool_hashes():
    root = Path(__file__).resolve().parents[1] / 'search_tool'
    hashes = {}
    for path in sorted(root.rglob('*')):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            hashes[str(path.relative_to(root))] = digest.hexdigest()
    return hashes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', default='I need a blue cotton dress.')
    args = parser.parse_args()
    problems = check_search_tool()
    if problems:
        print(json.dumps({'status': 'blocked', 'missing': problems}, indent=2))
        return 2
    before = tool_hashes()
    try:
        runtime = create_runtime()
        sid = runtime.new_session()['session_id']
        result = runtime.chat(sid, args.query)
        if runtime.agent.errors:
            raise RuntimeError(f'Search failed: {runtime.agent.errors[-1]}')
        if not result['products']:
            raise RuntimeError('No eligible products: live selection lifecycle not verified.')
        first_id = result['products'][0]['parent_asin']
        detail = runtime.product_detail(sid, first_id)
        assert detail['parent_asin'] == first_id
        ranks = ' and '.join(f"#{p['rank']}" for p in result['products'][:2])
        runtime.chat(sid, f'Compare {ranks}')
        final = runtime.chat(sid, 'Finalize my selection')
        assert final['selection_state']['status'] == 'finalized'
        assert not verify_audit(runtime.audit(sid))
        print(json.dumps({'status': 'passed', 'products': len(result['products']),
                          'selected': final['selection_state']['selected_asins'],
                          'backend': 'actual search_tool'}, indent=2))
    finally:
        if before != tool_hashes():
            raise RuntimeError('search_tool file set or content changed during live verification')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
