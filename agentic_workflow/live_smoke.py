"""Check actual search_tool inference and the full selection lifecycle."""
import argparse
import hashlib
import json
import re
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


def check_conversation(runtime):
    """Exercise recovery against actual search results, not injected candidates."""
    sid = runtime.new_session()['session_id']
    records = []

    def chat(message):
        result = runtime.chat(sid, message)
        assert not runtime.agent.errors, runtime.agent.errors
        assert not verify_audit(runtime.audit(sid))
        assert not re.search(r'[\u3400-\u9fff]', result['assistant']['message'])
        assert not re.search(r'[\u3400-\u9fff]', json.dumps(result['shopping_guide'], ensure_ascii=False))
        for product in result['products']:
            source = runtime.agent.get_catalog_product(product['parent_asin'])
            assert source, product['parent_asin']
            source_text = ' '.join((source.get('title', ''), str(source.get('features', [])),
                                    str(source.get('details', {})))).lower()
            for attribute in ('color', 'material'):
                expected = result['receipt']['hard'].get(attribute)
                if isinstance(expected, str):
                    assert re.search(r'\b' + re.escape(expected) + r'\b', source_text), (product['parent_asin'], attribute, expected)
            assert source.get('price') is None  # This dataset cannot verify the $30 target.
        records.append({'turn': result['turn'], 'message': message,
                        'products': len(result['products']),
                        'top_title': result['products'][0]['title'] if result['products'] else None,
                        'reason': result['receipt'].get('post_reason') or result['receipt'].get('pre_reason')})
        return result

    first = chat('I need a black cotton tshirt around $30')
    assert first['products']
    assert first['receipt']['hard']['color'] == 'black'
    assert 'price_max' not in first['receipt']['hard']
    detail_reply = chat('What material is the second one made of?')
    assert any(phrase in detail_reply['assistant']['message'] for phrase in
               ('The listing says:', 'The listing specifies', 'The listing describes it as pure cotton.'))
    assert [p['parent_asin'] for p in detail_reply['products']] == [p['parent_asin'] for p in first['products']]
    price_reply = chat('How much does #2 cost?')
    assert 'does not list a price' in price_reply['assistant']['message']
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, price_reply['turn'])['events']]
    baseline = runtime.agent.memory.snapshot(sid)
    pending_detail = runtime.agent.memory.pending[sid]
    for message in ('Is it pure cotton?', 'What about the price?', 'Is #2 waterproof?',
                    'Material, please', 'How about its fit?'):
        reply = chat(message)
        assert reply['receipt']['pre_reason'] == 'product_detail'
        assert reply['receipt']['focused_product_id'] == first['products'][1]['parent_asin']
        assert reply['receipt']['new_product_count'] == 0
        assert reply['receipt']['question'] is None
        assert [p['parent_asin'] for p in reply['products']] == [p['parent_asin'] for p in first['products']]
        state = runtime.agent.memory.snapshot(sid)
        assert state.hard_constraints == baseline.hard_constraints
        assert state.soft_preferences == baseline.soft_preferences
        assert state.exclusions == baseline.exclusions
        assert runtime.agent.memory.pending[sid] == pending_detail
        assert not any(e['stage'].startswith('2_retrieval') for e in runtime.agent.get_trace(sid, reply['turn'])['events'])
        if 'waterproof' in message:
            assert "can't reliably answer" in reply['assistant']['message']
        if 'price' in message:
            assert 'does not list a price' in reply['assistant']['message']
    proposal = chat('Maybe blue')
    assert proposal['receipt']['pre_reason'] == 'confirm_requirement_change'
    assert proposal['receipt']['hard']['color'] == 'black'
    pending = runtime.agent.memory.pending[sid].copy()
    paused = chat('let me think')
    assert runtime.agent.memory.pending[sid] == pending
    assert [p['parent_asin'] for p in paused['products']] == [p['parent_asin'] for p in first['products']]
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, paused['turn'])['events']]
    accepted = chat('replace')
    assert accepted['receipt']['hard']['color'] == 'blue'
    assert accepted['products']
    restored = chat('undo')
    assert restored['receipt']['hard']['color'] == 'black'
    assert restored['products']
    assert [p['parent_asin'] for p in restored['products']] == [p['parent_asin'] for p in first['products']]
    assert restored['receipt']['display_mode'] == 'restored_previous_results'
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, restored['turn'])['events']]
    uncertain = chat("I'm not sure")
    assert uncertain['receipt']['hard'] == restored['receipt']['hard']
    assert uncertain['assistant']['ask_attribute'] is None
    assert uncertain['products']
    assert [p['parent_asin'] for p in uncertain['products']] == [p['parent_asin'] for p in restored['products']]
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, uncertain['turn'])['events']]
    redone = chat('redo')
    assert redone['receipt']['hard']['color'] == 'blue'
    assert redone['receipt']['requirement_redo'] == 'applied'
    assert [p['parent_asin'] for p in redone['products']] == [p['parent_asin'] for p in accepted['products']]
    assert '2_retrieval' not in [e['stage'] for e in runtime.agent.get_trace(sid, redone['turn'])['events']]
    back = chat('undo')
    assert [p['parent_asin'] for p in back['products']] == [p['parent_asin'] for p in first['products']]
    ranks = ' and '.join(f"#{p['rank']}" for p in uncertain['products'][:2])
    chat(f'Compare {ranks}')
    final = chat('Finalize my selection')
    assert final['selection_state']['status'] == 'finalized'
    selected = final['selection_state']['selected_asins']
    expected_states = (
        ('Remove #2', selected[:1]),
        ('Undo selection', selected),
        ('Redo selection', selected[:1]),
        ('Undo shortlist', selected),
        ("Don't finalize yet", selected),
        ('Do not clear my shortlist', selected),
    )
    for message, expected in expected_states:
        result = chat(message)
        assert result['selection_state']['selected_asins'] == expected
        assert not result['selection_state']['finalized']
        assert result['receipt']['hard'] == back['receipt']['hard']
        assert [p['parent_asin'] for p in result['products']] == [p['parent_asin'] for p in back['products']]
        assert [p['parent_asin'] for p in result['selection_state']['selected_products']] == expected
        assert not any(e['stage'].startswith('2_retrieval') for e in runtime.agent.get_trace(sid, result['turn'])['events'])
    reconfirmed = chat('Please finalize my selection')
    assert reconfirmed['selection_state']['status'] == 'finalized'
    assert reconfirmed['selection_state']['selected_asins'] == selected
    assert runtime.agent.memory.snapshot(sid).soft_preferences['budget_target'][0].value == 30
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--query', default='I need a blue cotton dress.')
    parser.add_argument('--conversation', action='store_true', help='Also verify multi-turn correction, pause, undo and uncertainty')
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
        report = {'status': 'passed', 'products': len(result['products']),
                  'selected': final['selection_state']['selected_asins'],
                  'backend': 'actual search_tool'}
        if args.conversation:
            report['conversation'] = check_conversation(runtime)
    finally:
        if before != tool_hashes():
            raise RuntimeError('search_tool file set or content changed during live verification')
    report.update(search_tool_unchanged=True, verified_files=len(before))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
