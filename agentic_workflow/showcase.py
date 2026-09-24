"""Execute a Chinese shopping case and export a standalone replay webpage."""
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
    points = ''.join(f'<li>{escape(p["label"])}<small>商品原文：{escape(p["quote"])}</small></li>'
                     for p in notes['evidence'][:2])
    price = product.get('price')
    money = f'${price:.2f}' if isinstance(price, (int, float)) else '价格未知'
    return f'''<article class="product"><div class="product-head"><span>候选 {product['rank']:02}</span><b>{money}</b></div>
        <h3>{escape(product['title'])}</h3><p class="muted">{escape(product['parent_asin'])} · {escape(product['store'])}</p>
        <p>{escape(notes['feature'])}</p><p>{escape(notes['detail'])}</p><details><summary>商品原文</summary><ul>{points}</ul></details></article>'''


def run_case(backend='demo'):
    if backend == 'search_tool':
        from .runtime import create_runtime
        from .preflight import check_search_tool
        problems = check_search_tool()
        if problems:
            raise RuntimeError('; '.join(problems))
        runtime = create_runtime()
        prompts = ('我想找一条蓝色连衣裙。', '更喜欢棉质的。',
                   '比较第一个和第二个和第三个', '确认最终选择')
    else:
        runtime = AgentRuntime.create(DEMO_CATALOG, orchestration_mode='adaptive')
        prompts = DEMO_CASES['dress_zh']['prompts']
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


def render(report):
    labels = ['提出需求', '补充条件', '比较候选', '确认选择']
    sections = []
    for i, turn in enumerate(report['turns']):
        result = turn['result']
        receipt = result['receipt']
        state = {'必须满足': receipt.get('hard', {}), '偏好': receipt.get('soft', {}),
                 '排除': receipt.get('excluded', {})}
        cards = ''.join(product_card(p) for p in result['products'][:3])
        guide = build_shopping_guide(result['products'])
        rows = ''.join(f'<tr><td><b>#{p["rank"]}</b> {escape(p["title"])}</td><td>{escape(p["feature"]) or "—"}</td><td>{escape(p["detail"]) or "—"}</td></tr>' for p in guide['top_three'])
        comparison = f'<div class="table-wrap"><table><caption>前三款对比</caption><thead><tr><th>本次排行 / 商品</th><th>款式特点</th><th>其他信息</th></tr></thead><tbody>{rows}</tbody></table></div><p class="muted">{guide["data_note"]}</p>'
        others = ''.join(f'<article class="other-option"><b>#{p["rank"]} {escape(p["title"])}</b><p>{escape(" · ".join(filter(None, (p["feature"], p["detail"]))))}</p></article>' for p in guide['other_options'])
        remaining = f'<section class="remaining"><h3>其余几款，也各有值得留意的地方</h3>{others}</section>' if others else ''
        final = f'<div class="confirmed">✓ 已帮你保存 {len(report["handoff"]["selected_products"])} 款候选。先留着慢慢考虑，还没有下单。</div>' if i == 3 else ''
        sections.append(f'''<section class="step" id="step-{i}"><div class="step-heading"><span>0{i+1} / 04</span><h2>{labels[i]}</h2></div>
            <div class="columns"><div><div class="bubble user"><label>模拟用户 · 小林</label><p>{escape(turn['user'])}</p></div>
            <div class="bubble agent"><label>SHOPPING AGENT · 实际回复</label><p>{escape(result['assistant']['message'])}</p></div>
            <details><summary>查看该轮完整执行结果</summary><pre>{pretty(result)}</pre></details></div>
            <aside><h3>你的偏好，我记着呢</h3><p>这一轮整理了 {len(result['products'])} 款，最多展示 10 款。</p><p class="muted">{guide['ranking_note']}</p><details><summary>查看记住的条件</summary><pre>{pretty(state)}</pre></details></aside></div>
            {comparison}{final}<div class="products">{cards}</div>{remaining}</section>''')
    tabs = ''.join(f'<button class="tab" data-step="{i}" type="button">0{i+1} {label}</button>' for i, label in enumerate(labels))
    document = Path(__file__).with_name('showcase_template.html').read_text(encoding='utf-8')
    data = json.dumps(report, ensure_ascii=False).replace('<', '\\u003c')
    notice = ('本页是实际 search_tool 模型与商品索引跑出的结果，用户对话为示范脚本。价格、评分、库存未由该工具提供，所以不做推测。' if report.get('backend') == 'search_tool' else '本页使用内置模拟商品，由当前 workflow 实际执行，价格和评分为模拟数据。')
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
