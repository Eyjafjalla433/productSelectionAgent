"""Compact, source-backed notes. Missing facts stay absent."""
import re
from copy import deepcopy
from intent_router.option_labels import OPTION_LABELS_ZH
from shopping_agent.retrieval import style_evidence

ENGLISH_LABELS = {
    '亮片装饰': 'Sequin detailing', '主题服装': 'Costume style', '花卉图案': 'Floral print',
    '有口袋': 'Pockets', '裹身款': 'Wrap design', 'A 字版型': 'A-line cut',
    'V 领': 'V-neck', '圆领': 'Crew neck', '网眼设计': 'Mesh detailing',
    '长裙': 'Maxi length', '中长裙': 'Midi length', '短裙': 'Mini length',
    '长袖': 'Long sleeves', '短袖': 'Short sleeves', '无袖': 'Sleeveless',
    '可调节': 'Adjustable', '可机洗': 'Machine washable', '亚麻材质': 'Linen',
    '含棉': 'Contains cotton', '轻量款': 'Lightweight',
    '修身版型': 'Slim fit', '宽松版型': 'Loose fit', '常规版型': 'Regular fit',
}


FEATURES = (
    (r'\bsequins?\b', '亮片装饰', 'design'),
    (r'\b(costume|dirndl|oktoberfest)\b', '主题服装', 'design'),
    (r'\bfloral\b', '花卉图案', 'design'),
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


def describe(product, source_product=None):
    snippets = [str(product.get('title') or ''), *[str(v) for v in product.get('features', [])]]
    highlights = []
    # Preserve a short product-name fragment instead of inventing a design.
    title = snippets[0]
    name = re.split(r'\b(?:women\S*|men\S*|casual|stretchy|long|short|bodycon|soft|fitted|cotton|polyester|linen|t[ -]?shirts?|tees?|dress|shirt|size)\b', title, maxsplit=1, flags=re.I)[0].strip(' -(),')
    brand = str(product.get('store') or '').strip()
    if brand and name.casefold().startswith(brand.casefold() + ' '):
        name = name[len(brand):].strip()
    if 2 <= len(name.split()) <= 7 and len(name) <= 60 and name != title:
        highlights.append({'label': name, 'group': 'design', 'quote': title})
    source = product if source_product is None else source_product
    fits = [(label, style_evidence(source, value)) for value, label in (
        ('slim fit', '修身版型'), ('loose fit', '宽松版型'), ('regular fit', '常规版型'))]
    supported = [(label, quote) for label, quote in fits if quote]
    # Mixed-variant descriptions must not present opposing cuts as one fact.
    if len(supported) == 1:
        label, quote = supported[0]
        highlights.append({'label': label, 'group': 'design', 'quote': quote})
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
        highlights.append({'label': f'Listed size {label}', 'group': 'detail', 'quote': snippets[0]})
    for highlight in highlights:
        highlight['label'] = ENGLISH_LABELS.get(highlight['label'], highlight['label'])
    designs = [h['label'] for h in highlights if h['group'] == 'design']
    details = [h['label'] for h in highlights if h['group'] == 'detail']
    return {'rank': product['rank'], 'parent_asin': product['parent_asin'],
            'title': product['title'], 'feature': ' · '.join(designs[:3]),
            'detail': ' · '.join(details), 'fit': '', 'caution': '',
            'evidence': highlights, 'price': product.get('price')}


def build_shopping_guide(products):
    rows = [deepcopy(product['shopper_notes']) if 'shopper_notes' in product
            else describe(product) for product in products[:10]]
    return {'top_three': rows[:3], 'other_options': rows[3:], 'count': len(rows),
            'ranking_note': 'Ranked by search relevance, not sales.',
            'data_note': 'Prices are unavailable; budget fit still needs checking.' if any(p['price'] is None for p in rows) else '',
            'intro': (f'Here are {len(rows)} options. The table compares the top three.' if len(rows) > 3 else f'Let’s take a look at these {len(rows)} options.') if rows else ''}


def preference_tradeoff(products, locale='en'):
    """One shared caveat about an expressed preference, never a fabricated defect."""
    if not products:
        return ''
    labels = {**OPTION_LABELS_ZH, 'black': '黑色', 'white': '白色', 'blue': '蓝色', 'red': '红色',
              'cotton': '含棉', '100% cotton': '纯棉', 'linen': '亚麻',
              'casual': '休闲风格', 'formal': '正式风格'}
    for signal in products[0].get('match', {}).get('signals', ()):
        slot = signal.get('slot')
        if signal.get('tier') != 'soft' or slot not in {'color', 'material', 'style', 'size'}:
            continue
        count = sum(any(s.get('tier') == 'soft' and s.get('slot') == slot and s.get('status') == 'supported'
                        for s in p.get('match', {}).get('signals', ())) for p in products)
        if count == len(products):
            continue
        raw = signal.get('value', [])
        values = raw if isinstance(raw, (list, tuple)) else [raw]
        wanted = ' / '.join(labels.get(str(v), str(v)) if locale == 'zh' else str(v) for v in values)
        if locale == 'zh':
            return (f'其中 {count} 款的资料符合你偏好的{wanted}，其余先当备选。' if count
                    else f'你偏好的{wanted}，这批资料还没确认到；先把这些当作备选。')
        return (f'{count} of these have listed details supporting your preference for {wanted}; the others are alternatives.' if count
                else f'I could not confirm your preference for {wanted} in these listings, so treat them as alternatives.')
    return ''
