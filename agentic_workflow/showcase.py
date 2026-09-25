"""Execute an English shopping case and export a standalone replay webpage."""
import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

from mvp.audit import verify_audit
from mvp.demo import DEMO_CASES, DEMO_CATALOG
from mvp.server import AgentRuntime
from mvp.shopping_guide import describe, build_shopping_guide


def pretty(value):
    return escape(json.dumps(value, ensure_ascii=False, indent=2))


def product_card(product):
    notes = describe(product)
    points = ''.join(f'<li>{escape(p["label"])}<small>Source: {escape(p["quote"])}</small></li>'
                     for p in notes['evidence'][:2])
    price = product.get('price')
    money = f'${price:.2f}' if isinstance(price, (int, float)) else 'Price unavailable'
    return f'''<article class="product"><div class="product-head"><span>Option {product['rank']:02}</span><b>{money}</b></div>
        <h3>{escape(product['title'])}</h3><p class="muted">{escape(product['parent_asin'])} · {escape(product['store'])}</p>
        <p>{escape(notes['feature'])}</p><p>{escape(notes['detail'])}</p><details><summary>Source details</summary><ul>{points}</ul></details></article>'''


def run_case(backend='demo'):
    if backend == 'search_tool':
        from .runtime import create_runtime
        from .preflight import check_search_tool
        problems = check_search_tool()
        if problems:
            raise RuntimeError('; '.join(problems))
        runtime = create_runtime()
        prompts = ('I am looking for a blue dress.', 'I prefer cotton.',
                   'Compare #1, #2 and #3', 'Finalize my selection')
    else:
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        prompts = DEMO_CASES['dress_en']['prompts']
    sid = runtime.new_session()['session_id']
    turns = []
    for prompt in prompts:
        result = runtime.chat(sid, prompt)
        turns.append({'user': prompt, 'result': result})
        print(f"Turn {result['turn']}: {len(result['products'])} products", flush=True)
    handoff = runtime.selection_handoff(sid)
    audit = runtime.audit(sid)
    errors = verify_audit(audit)
    if errors or runtime.agent.errors:
        raise RuntimeError(f'Execution/audit failed: {errors or runtime.agent.errors}')
    expected = 3 if backend == 'search_tool' else 2
    if handoff['status'] != 'finalized' or len(handoff['selected_products']) != expected:
        raise RuntimeError(f'Expected a finalized {expected}-product shortlist')
    if any(not turn['result']['products'] for turn in turns):
        raise RuntimeError('The case did not produce product evidence on every turn')
    return {'generated_at': datetime.now(timezone.utc).isoformat(),
            'backend': backend,
            'data_source': 'actual search_tool model and index' if backend == 'search_tool' else 'synthetic catalog; actual local workflow execution',
            'turns': turns, 'handoff': handoff, 'audit': audit, 'audit_valid': True}


def description_section(handoff):
    result = (handoff or {}).get('comparison_assist') or {}
    if result.get('schema_version') != 'description-comparison.v1':
        return ''
    products = handoff['selected_products']
    titles = {product['parent_asin']: product['title'] for product in products}
    rows, missing = [], []
    for row in result['objective_comparison']['comparison_matrix']:
        if not any(row['values'].get(p['parent_asin'], {}).get('value') is not None for p in products):
            missing.append(row['dimension'].replace('_', ' '))
            continue
        cells = []
        for product in products:
            attribute = row['values'].get(product['parent_asin'], {})
            value = attribute.get('value')
            text = 'Unknown' if value is None else str(value)
            if attribute.get('source_type') == 'inferred':
                text += ' (Inference)'
            evidence = (f'<details><summary>View source</summary><p>{escape(str(attribute["evidence"]))}</p></details>'
                        if attribute.get('evidence') else '')
            cells.append(f'<td>{escape(text)}{evidence}</td>')
        rows.append(f'<tr><th>{escape(row["dimension"].replace("_", " ").title())}</th>{"".join(cells)}</tr>')
    headings = ''.join(f'<th>{escape(product["title"])}</th>' for product in products)
    facts = f'<div class="table-wrap"><table><thead><tr><th>Listed detail</th>{headings}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'
    unknowns = (f'<details><summary>Details not supplied ({len(missing)})</summary><p>Unknown: {escape(", ".join(missing))}</p></details>'
                if missing else '')

    def points(rows, fields):
        content = []
        for row in rows:
            entries = [point for field in fields for point in row.get(field, [])]
            if not entries:
                continue
            items = ''.join('<li>' + escape(point['text']) + '<details><summary>View source</summary>' +
                            ''.join('<p>' + escape(ref['quote']) + '</p>' for ref in point['evidence_refs']) +
                            '</details></li>' for point in entries)
            content.append(f'<h4>{escape(titles.get(row["parent_asin"], row["parent_asin"]))}</h4><ul>{items}</ul>')
        return ''.join(content)

    objective = points(result['objective_comparison']['product_assessments'], ('pros', 'cons'))
    personal = result['personalized_comparison']
    advice = points(personal['products'], ('fit_reasons', 'cautions')) if personal['personalization_applied'] else ''
    note = ('Direct catalog facts only; no model was configured.' if result['status'] == 'offline_preview' else
            'Some explanations could not be completed.' if result['status'] == 'partial' else
            'Based on the supplied listings. Inferences and unknowns need checking.')
    return ('<section class="selected-comparison"><h3>Your saved options: listed facts</h3>' + facts + unknowns +
            ('<h3>What stands out</h3>' + objective if objective else '') +
            ('<h3>For your preferences</h3>' + advice if advice else '') +
            f'<p class="muted">{note}</p></section>')


def render(report):
    labels = ['Your request', 'Refine preferences', 'Compare options', 'Save selection']
    sections = []
    for i, turn in enumerate(report['turns']):
        result = turn['result']
        receipt = result['receipt']
        state = {'Requirements': receipt.get('hard', {}), 'Preferences': receipt.get('soft', {}),
                 'Exclusions': receipt.get('excluded', {})}
        cards = ''.join(product_card(p) for p in result['products'][:3])
        guide = build_shopping_guide(result['products'])
        takeaway = (f'<p class="comparison-takeaway">{escape(guide["comparison_takeaway"])}</p>'
                    if guide['comparison_takeaway'] else '')
        rows = ''.join(f'<tr><td><b>#{p["rank"]}</b> {escape(p["title"])}</td><td>{escape(p["feature"]) or "—"}</td><td>{escape(p["detail"]) or "—"}</td></tr>' for p in guide['top_three'])
        comparison = f'<div class="table-wrap"><table><caption>Top three comparison</caption><thead><tr><th>Rank / Product</th><th>Standout features</th><th>Other details</th></tr></thead><tbody>{rows}</tbody></table></div>{takeaway}<p class="muted">{guide["data_note"]}</p>'
        others = ''.join(f'<article class="other-option"><b>#{p["rank"]} {escape(p["title"])}</b><p>{escape(" · ".join(filter(None, (p["feature"], p["detail"]))))}</p></article>' for p in guide['other_options'])
        remaining = f'<section class="remaining"><h3>More options to consider</h3>{others}</section>' if others else ''
        final = f'<div class="confirmed">✓ Saved {len(report["handoff"]["selected_products"])} options for later. No order has been placed.</div>' if i == 3 else ''
        sections.append(f'''<section class="step" id="step-{i}"><div class="step-heading"><span>0{i+1} / 04</span><h2>{labels[i]}</h2></div>
            <div class="columns"><div><div class="bubble user"><label>DEMO SHOPPER · ALEX</label><p>{escape(turn['user'])}</p></div>
            <div class="bubble agent"><label>SHOPPING AGENT · ACTUAL RESPONSE</label><p>{escape(result['assistant']['message'])}</p></div>
            <details><summary>View the full turn result</summary><pre>{pretty(result)}</pre></details></div>
            <aside><h3>Your preferences, remembered</h3><p>Showing {len(result['products'])} options this turn, up to 10.</p><p class="muted">{guide['ranking_note']}</p><details><summary>View remembered requirements</summary><pre>{pretty(state)}</pre></details></aside></div>
            {comparison}{description_section(result.get('handoff'))}{final}<div class="products">{cards}</div>{remaining}</section>''')
    tabs = ''.join(f'<button class="tab" data-step="{i}" type="button">0{i+1} {label}</button>' for i, label in enumerate(labels))
    document = Path(__file__).with_name('showcase_template.html').read_text(encoding='utf-8')
    data = json.dumps(report, ensure_ascii=False).replace('<', '\\u003c')
    notice = ('This replay uses the actual search_tool model and product index with a scripted shopper. The tool does not supply prices, ratings, or stock, so these are not inferred.' if report.get('backend') == 'search_tool' else 'This replay runs the actual workflow on synthetic products. Prices and ratings are simulated.')
    return document.replace('<!--TABS-->', tabs).replace('<!--STEPS-->', ''.join(sections)).replace('/*REPORT*/', data).replace('<!--NOTICE-->', notice)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('showcase_output'))
    parser.add_argument('--backend', choices=('demo', 'search_tool'), default='demo')
    args = parser.parse_args()
    report = run_case(args.backend)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'case.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    page = args.output / 'index.html'
    page.write_text(render(report), encoding='utf-8')
    print(json.dumps({'page': str(page.resolve()), 'turns': len(report['turns']),
        'status': report['handoff']['status'], 'audit_valid': report['audit_valid'],
        'selected': report['handoff']['decision']['selected_asins']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
