"""Execute a Chinese shopping case and export a standalone replay webpage."""
import argparse
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

from mvp.audit import verify_audit
from mvp.demo import DEMO_CASES, DEMO_CATALOG
from mvp.server import AgentRuntime


def pretty(value):
    return escape(json.dumps(value, ensure_ascii=False, indent=2))


def product_card(product):
    advice = product['advice']
    points = ''.join(f'<li>{escape(p["text"])}<small>{escape(p["source"])} · {escape(p["evidence"])}</small></li>'
                     for p in advice['pros'][:2])
    warnings = ''.join(f'<li>{escape(p["text"])}<small>{escape(p["evidence"])}</small></li>'
                       for p in advice['cons'][:2])
    price = product.get('price')
    money = f'${price:.2f}' if isinstance(price, (int, float)) else '价格未知'
    return f'''<article class="product"><div class="product-head"><span>候选 {product['rank']:02}</span><b>{money}</b></div>
        <h3>{escape(product['title'])}</h3><p class="muted">{escape(product['parent_asin'])} · {escape(product['store'])}</p>
        <h4>适合的理由 · 原始商品证据</h4><ul>{points}</ul><h4>仍需注意</h4><ul class="caution">{warnings}</ul></article>'''


def run_case():
    runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
    sid = runtime.new_session()['session_id']
    turns = []
    for prompt in DEMO_CASES['dress_zh']['prompts']:
        result = runtime.chat(sid, prompt)
        turns.append({'user': prompt, 'result': result})
    handoff = runtime.selection_handoff(sid)
    audit = runtime.audit(sid)
    errors = verify_audit(audit)
    if errors or runtime.agent.errors:
        raise RuntimeError(f'Execution/audit failed: {errors or runtime.agent.errors}')
    if handoff['status'] != 'finalized' or len(handoff['selected_products']) != 2:
        raise RuntimeError('Expected a finalized two-product shortlist')
    if any(not turn['result']['products'] for turn in turns):
        raise RuntimeError('The case did not produce product evidence on every turn')
    return {'generated_at': datetime.now(timezone.utc).isoformat(),
            'data_source': 'synthetic catalog; actual local workflow execution',
            'turns': turns, 'handoff': handoff, 'audit': audit, 'audit_valid': True}


def render(report):
    labels = ['提出需求', '补充条件', '比较候选', '确认选择']
    sections = []
    for i, turn in enumerate(report['turns']):
        result = turn['result']
        receipt = result['receipt']
        state = {'必须满足': receipt.get('hard', {}), '偏好': receipt.get('soft', {}),
                 '排除': receipt.get('excluded', {})}
        cards = ''.join(product_card(p) for p in result['products'][:3])
        comparison = ''
        if i >= 2:
            rows = ''.join(f'<tr><td>{escape(p["title"])}</td><td>${p["price"]:.2f}</td><td>{p["average_rating"]}</td><td>{p["requirement_match"]["hard_supported"]}/{p["requirement_match"]["hard_total"]}</td></tr>' for p in report['handoff']['selected_products'])
            comparison = f'<div class="table-wrap"><table><caption>已选两款 · 后端选型结果</caption><thead><tr><th>商品</th><th>模拟价格</th><th>模拟评分</th><th>硬条件证据</th></tr></thead><tbody>{rows}</tbody></table></div>'
        final = '<div class="confirmed">✓ 用户已确认这两款候选 · 选型单已生成，未下单</div>' if i == 3 else ''
        sections.append(f'''<section class="step" id="step-{i}"><div class="step-heading"><span>0{i+1} / 04</span><h2>{labels[i]}</h2></div>
            <div class="columns"><div><div class="bubble user"><label>模拟用户 · 小林</label><p>{escape(turn['user'])}</p></div>
            <div class="bubble agent"><label>SHOPPING AGENT · 实际回复</label><p>{escape(result['assistant']['message'])}</p></div>
            <details><summary>查看该轮完整执行结果</summary><pre>{pretty(result)}</pre></details></div>
            <aside><h3>这一轮记住了什么</h3><pre>{pretty(state)}</pre><p class="muted">会话第 {result['turn']} 轮 · {len(result['products'])} 个展示候选</p></aside></div>
            {comparison}{final}<div class="products">{cards}</div></section>''')
    tabs = ''.join(f'<button class="tab" data-step="{i}" type="button">0{i+1} {label}</button>' for i, label in enumerate(labels))
    document = Path(__file__).with_name('showcase_template.html').read_text(encoding='utf-8')
    data = json.dumps(report, ensure_ascii=False).replace('<', '\\u003c')
    return document.replace('<!--TABS-->', tabs).replace('<!--STEPS-->', ''.join(sections)).replace('/*REPORT*/', data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('showcase_output'))
    args = parser.parse_args()
    report = run_case()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'case.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    page = args.output / 'index.html'
    page.write_text(render(report), encoding='utf-8')
    print(json.dumps({'page': str(page.resolve()), 'turns': len(report['turns']),
        'status': report['handoff']['status'], 'audit_valid': report['audit_valid'],
        'selected': report['handoff']['decision']['selected_asins']}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
