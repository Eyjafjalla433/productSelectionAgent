"""Compact, source-backed notes. Missing facts stay absent."""
import re


FEATURES = (
    (r'\bsequins?\b', '亮片装饰', 'design'),
    (r'\b(costume|dirndl|oktoberfest)\b', '主题服装', 'design'),
    (r'\bfloral\b', '花卉图案', 'design'),
    (r'\b(?:bodycon|fitted|slim fit)\b', '修身版型', 'design'),
    (r'\b(?:relaxed fit|loose fit|oversized)\b', '宽松版型', 'design'),
    (r'\bpockets?\b', '有口袋', 'design'),
    (r'\bwrap\b', '裹身款', 'design'),
    (r'\ba[ -]line\b', 'A 字版型', 'design'),
    (r'\bv[ -]neck\b', 'V 领', 'design'),
    (r'\bcrew neck\b', '圆领', 'design'),
    (r'\bmesh\b', '网眼设计', 'design'),
    (r'\bmaxi\b', '长裙', 'detail'),
    (r'\bmidi\b', '中长裙', 'detail'),
    (r'\bmini\b', '短裙', 'detail'),
    (r'\blong sleeves?\b', '长袖', 'detail'),
    (r'\bshort sleeves?\b', '短袖', 'detail'),
    (r'\bsleeveless\b', '无袖', 'detail'),
    (r'\badjustable\b', '可调节', 'detail'),
    (r'\bmachine wash(?:able)?\b', '可机洗', 'detail'),
    (r'\blinen\b', '亚麻材质', 'detail'),
    (r'\bcotton\b', '含棉', 'detail'),
    (r'\blightweight\b', '轻量款', 'detail'),
)


def describe(product):
    snippets = [str(product.get('title') or ''), *[str(v) for v in product.get('features', [])]]
    highlights = []
    # Preserve a short product-name fragment instead of inventing a design.
    title = snippets[0]
    name = re.split(r'\b(?:women\S*|men\S*|casual|stretchy|long|short|bodycon|soft|fitted|cotton|polyester|linen|t[ -]?shirts?|tees?|dress|shirt|size)\b', title, maxsplit=1, flags=re.I)[0].strip(' -(),')
    brand = str(product.get('store') or '').strip()
    if brand and name.casefold().startswith(brand.casefold() + ' '):
        name = name[len(brand):].strip()
    if 2 <= len(name.split()) <= 7 and len(name) <= 60 and name != title:
        highlights.append({'label': name + ' 款', 'group': 'design', 'quote': title})
    for pattern, label, group in FEATURES:
        for text in snippets:
            match = re.search(pattern, text, re.I)
            if not match or re.search(r'\b(?:no|not|without|free of)\s*$', text[:match.start()], re.I):
                continue
            highlights.append({'label': label or match.group() + ' 款', 'group': group, 'quote': text})
            break
    # Title size is not a claim about availability or recommended fit.
    size = re.search(r'\b(XXXL|XXL|XL|XXS|XS|Medium|Small|Large)\b|\bsize\s+([SML])\b', snippets[0], re.I)
    if size:
        value = (size.group(1) or size.group(2)).upper()
        label = {'MEDIUM': 'M', 'SMALL': 'S', 'LARGE': 'L'}.get(value, value)
        highlights.append({'label': f'标题尺码 {label}', 'group': 'detail', 'quote': snippets[0]})
    designs = [h['label'] for h in highlights if h['group'] == 'design']
    details = [h['label'] for h in highlights if h['group'] == 'detail']
    return {'rank': product['rank'], 'parent_asin': product['parent_asin'],
            'title': product['title'], 'feature': ' · '.join(designs[:3]),
            'detail': ' · '.join(details), 'fit': '', 'caution': '',
            'evidence': highlights, 'price': product.get('price')}


def build_shopping_guide(products):
    rows = [describe(product) for product in products[:10]]
    return {'top_three': rows[:3], 'other_options': rows[3:], 'count': len(rows),
            'ranking_note': '按搜索相关性排序，不是销量榜。',
            'data_note': '价格未提供，预算是否符合还需核实。' if any(p['price'] is None for p in rows) else '',
            'intro': (f'先看这 {len(rows)} 款，前三款的差别放在表格里。' if len(rows) > 3 else f'先看看这 {len(rows)} 款。') if rows else ''}
