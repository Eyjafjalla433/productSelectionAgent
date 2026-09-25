"""Bounded, extractive answers about an explicitly referenced displayed item."""
import math
import re
from shopping_agent.retrieval import pure_cotton_matches, style_evidence


FIBERS_ZH = {'cotton': '棉', 'polyester': '聚酯纤维', 'linen': '亚麻',
             'wool': '羊毛', 'silk': '丝', 'nylon': '尼龙', 'rayon': '人造丝',
             'viscose': '粘胶纤维', 'spandex': '氨纶', 'elastane': '氨纶',
             'acrylic': '腈纶', 'modal': '莫代尔', 'cashmere': '羊绒'}


def composition_summary(snippets, locale='zh'):
    """Only summarize a complete, consistent percentage listing, never infer it."""
    text = ' '.join(map(str, snippets)).lower()
    labels = FIBERS_ZH if locale == 'zh' else {fiber: ('spandex' if fiber == 'elastane' else fiber) for fiber in FIBERS_ZH}
    matches = list(re.finditer(r'(?<![\w.\-])(\d+(?:\.\d+)?)\s*%\s*(' + '|'.join(FIBERS_ZH) + r')\b', text))
    amounts = {}
    for match in matches:
        if re.search(r'\b(?:not|no|without|approximately|about|up to|at least)\b[^.;]{0,30}$', text[:match.start()]):
            return None, True
        amount = float(match.group(1))
        fiber = labels[match.group(2)]
        if amount <= 0 or amount > 100:
            return None, True
        if fiber in amounts and amounts[fiber] != amount:
            return None, True
        amounts[fiber] = amount
    total = sum(amounts.values())
    mentioned = {labels[m.group()] for m in re.finditer(r'\b(?:' + '|'.join(FIBERS_ZH) + r')\b', text)}
    if amounts and mentioned - amounts.keys():
        return None, True
    if not amounts or not math.isclose(total, 100, abs_tol=0.01):
        return None, bool(amounts and total > 100)
    # Conditional/part-specific composition cannot stand for the entire item.
    if re.search(r'\b(?:except|heather|lining|shell|trim|depending|varies)\b|里料|辅料|部分颜色', text):
        return None, True
    if locale == 'zh':
        return '、'.join(f'{fiber} {amount:g}%' for fiber, amount in amounts.items()), False
    return ', '.join(f'{amount:g}% {fiber}' for fiber, amount in amounts.items()), False


def answer_product_question(product, rank, attribute, locale):
    prefix = f'第 {rank} 款：' if locale == 'zh' else f'#{rank}: '
    if attribute.startswith('material_check:'):
        wanted = attribute.removeprefix('material_check:')
        pure = wanted.startswith('pure ')
        fiber = wanted.removeprefix('pure ')
        if pure and fiber != 'cotton':
            return prefix + f"I can't confirm that {fiber} purity from the available listing."
        details = product.get('details') or {}
        snippets = [str(value) for key, value in details.items() if re.search(r'material|fabric|composition', key, re.I)] if isinstance(details, dict) else []
        for key in ('features', 'description', 'title'):
            raw = product.get(key) or []
            snippets.extend([raw] if isinstance(raw, str) else list(raw))
        summary, ambiguous = composition_summary(snippets, 'en')
        if ambiguous:
            return prefix + 'I cannot confirm this from the listing because its composition varies or conflicts. Check the exact variant.'
        if summary:
            amounts = {name: float(amount) for amount, name in re.findall(r'(\d+(?:\.\d+)?)%\s+([a-z]+)', summary)}
            supported = amounts.get(fiber, 0) == 100 and len(amounts) == 1 if pure else amounts.get(fiber, 0) > 0
            return prefix + ('Yes. ' if supported else 'No. ') + f'The listing specifies {summary}.'
        if pure and pure_cotton_matches(product):
            return prefix + 'Yes. The listing describes it as pure cotton.'
        return prefix + f"I can't confirm whether it is {'pure ' if pure else ''}{fiber} from the available composition details."
    if attribute == 'unsupported':
        return prefix + ("I can't reliably answer that product question yet. I can check its listed material, price, or fit. Your shopping preferences are unchanged.")
    if attribute == 'fit':
        fits = [(value, label) for value, label in (
            ('slim fit', '修身版型'), ('loose fit', '宽松版型'), ('regular fit', '常规版型'))
            if style_evidence(product, value)]
        if len(fits) == 1:
            value, label = fits[0]
            return prefix + (f'资料标注为{label}。实际松紧还要结合尺码表，不能只凭版型判断是否合身。' if locale == 'zh'
                             else f'The listing describes a {value}. Check the size chart to judge how it would fit you.')
        if fits:
            return prefix + ('资料里有不同的版型描述，暂时不能确定这款是哪一种。' if locale == 'zh'
                             else 'The listing contains conflicting fit descriptions, so I cannot confirm this item’s cut.')
        return prefix + ('资料没有明确版型，暂时不能确认是否宽松或修身。' if locale == 'zh'
                         else 'The listing does not clearly specify whether this item is loose or fitted.')
    if attribute == 'price':
        try:
            price = float(product.get('price'))
            if not math.isfinite(price) or price < 0:
                raise ValueError('Invalid price')
        except (ValueError, TypeError):
            return prefix + ('资料没有给出价格，暂时没法确认多少钱。' if locale == 'zh'
                             else "This source does not list a price, so I can't confirm its cost.")
        return prefix + (f'资料标价 ${price:g}，不是实时售价。' if locale == 'zh'
                         else f'The catalog lists ${price:g}; this is not a live price.')
    details = product.get('details') or {}
    snippets = [str(value) for key, value in details.items() if re.search(r'material|fabric|composition', key, re.I)] if isinstance(details, dict) else []
    for key in ('features', 'description', 'title'):
        raw = product.get(key) or []
        snippets.extend([raw] if isinstance(raw, str) else list(raw))
    pattern = re.compile(r'\b(?:' + '|'.join(FIBERS_ZH) + r'|leather)\b|纯棉|混纺|聚酯|棉质', re.I)
    quotes = []
    for snippet in snippets:
        for sentence in re.split(r'[\n;；。]|(?<=[a-zA-Z])\.\s+', str(snippet)):
            sentence = ' '.join(sentence.split())
            if pattern.search(sentence) and sentence not in quotes:
                quotes.append(sentence)
    if not quotes:
        return prefix + ('资料里没有明确的材质说明，暂时不能确认。' if locale == 'zh'
                         else "The listing does not provide clear material details.")
    summary, ambiguous = composition_summary(snippets, locale)
    if summary:
        return prefix + (f'资料标注：{summary}。' if locale == 'zh' else f'The listing specifies {summary}.')
    if not ambiguous and pure_cotton_matches(product):
        return prefix + ('资料标注为纯棉。' if locale == 'zh' else 'The listing describes it as pure cotton.')
    excerpt = ' / '.join(quote[:240] + ('…' if len(quote) > 240 else '') for quote in quotes[:2])
    caution = ('成分描述带有限定或不一致，需确认具体款式。' if locale == 'zh'
               else 'The composition includes qualifications or inconsistent details; check the specific item. ') if ambiguous else ''
    return prefix + caution + (f'材质资料写的是：{excerpt}' if locale == 'zh'
                               else f'The listing says: {excerpt}')
