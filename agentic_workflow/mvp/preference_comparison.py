"""Conservative, read-only comparison of currently displayed catalog items."""

from __future__ import annotations

import math
import re
from typing import Any, Callable

from intent_router.turn_router import OPTION_ACCEPTANCE
from .similarity import supported_facets


def _price(value: Any) -> float | None:
    try:
        price = float(str(value).replace('$', '').replace(',', ''))
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price >= 0 else None


def _signal_label(signal: dict[str, Any]) -> str:
    value = signal.get('value')
    if isinstance(value, (list, tuple)):
        value = ', '.join(str(item) for item in value)
    slot = signal.get('slot', 'preference')
    return str(value) if slot in {'color', 'material', 'style', 'size', 'brand'} else f"{slot.replace('_', ' ')} {value}"


def _distinguishing_question(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Ask at most one optional question about a verified difference."""
    if len(rows) != 2:
        return None
    for facet, label in (('fit', 'fit'), ('color', 'color')):
        values = [row['verified_facets'].get(facet) for row in rows]
        if all(values) and values[0] != values[1]:
            return {
                'slot': 'style' if facet == 'fit' else facet,
                'options': [{'rank': row['rank'], 'value': value, 'parent_asin': row['parent_asin']}
                            for row, value in zip(rows, values)],
                'message': (f"The clearest difference is {label}: #{rows[0]['rank']} is {values[0]} "
                            f"and #{rows[1]['rank']} is {values[1]}. Do you prefer either, or "
                            "should I leave the ranking as it is?"),
            }
    return None


def resolve_comparison_reply(message: str, question: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve a short reference, optionally followed by another shopping detail."""
    if not question or len(question.get('options', ())) != 2:
        return None
    text = message.strip().casefold().rstrip(' .!?')
    if question.get('kind') == 'decision_tradeoff':
        parts = re.fullmatch(r'(?P<ref>[^,]+?)(?:\s*,\s*(?:(?:but|and)\s+)?(?P<extra>.+))?', text)
        if not parts:
            return None
        reference = re.sub(r'^(?:i (?:prefer|care (?:more )?about)|the)\s+', '', parts.group('ref')).strip()
        choices = question['options']
        if reference in {'the first one', 'first one', 'first option'}:
            option = choices[0]
        elif reference in {'the second one', 'second one', 'second option'}:
            option = choices[1]
        else:
            matched = [item for item in choices
                       if reference in {str(item['value']).casefold(), item['facet'],
                                        'material' if item['facet'] == 'fabric' else item['facet'],
                                        f"#{item['rank']}"}
                       or (item['facet'] == 'fabric' and reference == 'cotton'
                           and 'cotton' in str(item['value']).casefold())]
            option = matched[0] if len(matched) == 1 else None
        return {'kind': 'decision_tradeoff', **option,
                'extra_message': parts.group('extra')} if option else None
    parts = re.fullmatch(
        r'(?P<ref>(?:the )?(?:first|second)(?: one| item| option)?|#(?:1|2))'
        r'(?:(?:\s*,\s*(?:(?:but|and)\s+)?|\s+(?:but|and)\s+)(?P<extra>.+))?',
        text,
    )
    if not parts:
        return None
    text = parts.group('ref')
    ordinal = (0 if re.fullmatch(r'(?:the )?first(?: one| item| option)?', text) else
               1 if re.fullmatch(r'(?:the )?second(?: one| item| option)?', text) else None)
    if ordinal is not None:
        option = question['options'][ordinal]
    elif re.fullmatch(r'#(?:1|2)', text):
        option = next((item for item in question['options'] if item['rank'] == int(text[1:])), None)
        if option is None:
            return None
    else:
        return None
    return {'slot': question['slot'], **option,
            'extra_message': parts.group('extra')}


def declines_comparison_question(message: str, question: dict[str, Any] | None) -> bool:
    """A declined optional distinction is not a new preference or rejection."""
    if not question:
        return False
    text = message.strip().casefold().rstrip(' .!?')
    return bool(re.fullmatch(OPTION_ACCEPTANCE, text)) or text in {
                    'neither', 'neither one', 'no preference', 'no preference between them',
                    'either is fine', 'any is fine', "doesn't matter", 'not sure',
                    "i don't know", 'leave the ranking', 'leave the ranking as it is'}


def decision_followup(
    products: list[dict[str, Any]], selection: dict[str, Any],
    catalog_lookup: Callable[[str], dict[str, Any] | None],
) -> tuple[str, dict[str, Any]]:
    """Offer a starting pick only when the new page verifies the chosen facet."""
    facet = 'fabric' if selection['slot'] == 'material' else 'fit' if selection['slot'] == 'style' else selection['slot']
    wanted = str(selection['value']).casefold()
    matches = []
    for product in products:
        source = catalog_lookup(product['parent_asin']) or {}
        supported = supported_facets(source).get(facet)
        if supported and str(supported['value']).casefold() == wanted:
            matches.append(product)
    if not matches:
        message = (f"I've saved {selection['value']} as a preference, but I can't verify it "
                   "on any item in this result set. I wouldn't pick one for you on that basis. "
                   "You can keep browsing or change the preference.")
        return message, {'recommended_rank': None, 'verified_matches': 0,
                         'basis': 'verified current-page catalog facet'}
    pick = matches[0]
    others = len(matches) - 1
    message = (f"Since {selection['value']} matters to you, start with #{pick['rank']}: "
               f"{pick['title']}. Its listing explicitly supports that detail. ")
    if others:
        message += (f"{others} other displayed {'item' if others == 1 else 'items'} "
                    "also support it, so this isn't a unique winner. ")
    message += ("This is a catalog-based starting pick, not proof of quality or fit. "
                "Check the current price, size, and availability before buying.")
    return message, {'recommended_rank': pick['rank'], 'parent_asin': pick['parent_asin'],
                     'verified_matches': len(matches),
                     'basis': 'verified current-page catalog facet'}


def compare_preferences(
    products: list[dict[str, Any]], catalog_lookup: Callable[[str], dict[str, Any] | None],
    *, decision_help: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Never claim personal fit, quality, live price, or stock from rank alone."""
    rows = []
    for product in products:
        source = catalog_lookup(product['parent_asin']) or {}
        facets = supported_facets(source)
        signals = (product.get('match') or {}).get('signals') or []
        supported = [_signal_label(signal) for signal in signals
                     if signal.get('tier') == 'soft' and signal.get('status') == 'supported'
                     and signal.get('slot') != 'budget_target']
        rows.append({
            'rank': product['rank'], 'parent_asin': product['parent_asin'],
            'title': product['title'],
            'verified_facets': {key: facet['value'] for key, facet in facets.items()},
            'supported_preferences': supported,
            'hard_supported': (product.get('match') or {}).get('hard_supported', 0),
            'price': _price(product.get('price')),
        })

    supported_sets = [set(row['supported_preferences']) for row in rows]
    winner = None
    if len(rows) >= 2:
        leaders = [index for index, signals in enumerate(supported_sets)
                   if signals and all(signals > other for j, other in enumerate(supported_sets) if j != index)
                   and all(rows[index]['hard_supported'] >= other['hard_supported']
                           for j, other in enumerate(rows) if j != index)]
        if len(leaders) == 1:
            winner = rows[leaders[0]]['rank']

    if winner is None:
        opening = ("I wouldn't call one a clear winner from these listings alone. Here's what I can verify:"
                   if decision_help else
                   "I can't confidently choose one for you yet. The list order reflects available "
                   "match signals, not proven quality or how these items will fit you.")
    else:
        opening = (f"#{winner} is a tentative match for your stated preferences because its listing "
                   "supports more of them. That does not verify quality or how it will fit you.")

    shared_facets = {key: value for key, value in rows[0]['verified_facets'].items()
                     if all(row['verified_facets'].get(key) == value for row in rows[1:])}
    shared_preferences = set.intersection(*(set(row['supported_preferences']) for row in rows))
    prices = [row['price'] for row in rows]
    shared_price = prices[0] if all(price is not None and price == prices[0] for price in prices) else None
    descriptions = []
    for row in rows:
        details = [value for key, value in row['verified_facets'].items() if key not in shared_facets]
        distinct_preferences = [value for value in row['supported_preferences']
                                if value not in shared_preferences]
        if distinct_preferences:
            details.append('matches your ' + ', '.join(distinct_preferences) + ' preference')
        if row['price'] is not None and shared_price is None:
            details.append(f"catalog price ${row['price']:g}")
        if details:
            descriptions.append(f"#{row['rank']}: " + '; '.join(details))
    shared_details = list(shared_facets.values())
    if shared_preferences:
        shared_details.append('matches your ' + ', '.join(sorted(shared_preferences)) + ' preference')
    if shared_price is not None:
        shared_details.append(f'catalog price ${shared_price:g}')
    if shared_details:
        descriptions.insert(0, 'Shared listing details: ' + '; '.join(shared_details))
    if not descriptions:
        descriptions.append("The listings don't show a verified difference I can use to choose.")
    price_note = ("Only some items have a catalog price, so value is still unclear."
                  if any(price is None for price in prices) and any(price is not None for price in prices)
                  else 'Catalog prices are not live offers.' if all(price is not None for price in prices)
                  else None)
    next_question = _distinguishing_question(rows) if winner is None else None
    message = opening + '\n\n' + '\n'.join(descriptions)
    if price_note:
        message += '\n' + price_note
    if next_question:
        message += '\n\n' + next_question['message']
    elif decision_help and winner is None and len(rows) >= 3:
        clues = []
        for facet in ('fit', 'fabric', 'color'):
            for row in rows:
                value = row['verified_facets'].get(facet)
                if value and shared_facets.get(facet) != value:
                    clues.append((row['rank'], facet, value))
                    break
            if len(clues) >= 2:
                break
        if len(clues) >= 2:
            first, second = clues[:2]
            prompt = (f"I can verify {first[2]} for #{first[0]} and {second[2]} for "
                      f"#{second[0]}. Which of those details matters more to you? "
                      "An unlisted detail is unknown, not a drawback.")
            message += '\n\n' + prompt
            next_question = {
                'kind': 'decision_tradeoff', 'message': prompt,
                'options': [{'rank': rank, 'facet': facet,
                             'slot': 'style' if facet == 'fit' else 'material' if facet == 'fabric' else facet,
                             'value': value,
                             'parent_asin': next(row['parent_asin'] for row in rows if row['rank'] == rank)}
                            for rank, facet, value in clues[:2]],
            }
        elif clues:
            rank, _, value = clues[0]
            message += (f"\n\n#{rank} explicitly lists {value}. Does that matter to you, "
                        "or would you like to keep the other options open?")
    return message, {'rows': rows, 'tentative_winner_rank': winner,
                     'next_question': next_question['message'] if next_question else None,
                     'question': next_question,
                     'basis': 'displayed listing facts and current-session preferences only'}
