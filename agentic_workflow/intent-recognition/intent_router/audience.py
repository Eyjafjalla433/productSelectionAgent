"""Explicit catalog audience labels; never infer them from clothing category."""
import re

ALIASES = {
    'men': r"men(?:['’]?s)?|man|male|男士|男款|男装",
    'women': r"women(?:['’]?s)?|woman|female|ladies|女士|女款|女装",
    'boys': r"boys?(?:['’]s)?|男童",
    'girls': r"girls?(?:['’]s)?|女童",
    'kids': r"kids?(?:['’]s)?|children|child|youth|儿童|童装",
    'baby': r"bab(?:y|ies)|infant|toddler|婴儿|幼儿",
    'unisex': r"unisex|男女通用",
}


def mentions(text):
    return [(key, m) for key, pattern in ALIASES.items()
            for m in re.finditer(r'(?<!\w)(?:' + pattern + r')(?!\w)', str(text), re.I)]


def audience_values(text):
    return {key for key, _ in mentions(text)}


def product_audiences(product):
    details = product.get('details') or {}
    departments = [str(v) for k, v in details.items()
                   if str(k).casefold() in {'department', 'gender', 'age range', 'age range description'}] if isinstance(details, dict) else []
    title_labels = audience_values(product.get('title', ''))
    labels = audience_values(' '.join(departments))
    if labels:
        labels |= title_labels  # Contradictory title labels must not bypass the filter.
    if not labels:
        labels = title_labels
    if not labels:
        labels = audience_values(str(product.get('categories') or ''))
    return labels


def audience_matches(product, value):
    labels = product_audiences(product)
    target = next(iter(audience_values(value)), str(value))
    children = {'boys', 'girls', 'kids', 'baby'}
    if target in {'men', 'women'}:
        opposite = 'women' if target == 'men' else 'men'
        return not (labels & children) and (target in labels or 'unisex' in labels) and not (opposite in labels and 'unisex' not in labels)
    if target == 'kids':
        return bool(labels & {'boys', 'girls', 'kids'}) and 'baby' not in labels
    return target in labels
