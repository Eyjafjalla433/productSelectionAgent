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
    value = signal.get('matched_values', signal.get('value'))
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


def _choice_reference(reference: str, question: dict[str, Any]) -> dict[str, Any] | None:
    """Resolve one complete reference, without interpreting negation or edits."""
    choices = question['options']
    ordinal = re.fullmatch(r'(?:the )?(first|second)(?: one| item| option)?', reference)
    if ordinal:
        return choices[0 if ordinal.group(1) == 'first' else 1]
    reference = reference.removeprefix('the ')
    matches = []
    for item in choices:
        aliases = {str(item['value']).casefold(), f"#{item['rank']}"}
        if question.get('kind') == 'decision_tradeoff':
            aliases.add(item['facet'])
            if item['facet'] == 'fabric':
                aliases.add('material')
                if 'cotton' in str(item['value']).casefold():
                    aliases.add('cotton')
        if reference in aliases:
            matches.append(item)
    return matches[0] if len(matches) == 1 else None


def resolve_comparison_reply(message: str, question: dict[str, Any] | None) -> dict[str, Any] | None:
    """Resolve a short reference, optionally followed by another shopping detail."""
    if not question or len(question.get('options', ())) != 2:
        return None
    text = message.strip().casefold().rstrip(' .!?')
    correction = re.fullmatch(r'not\s+(.+?)\s*(?:,|—|;|\sbut\s)\s*(.+)', text)
    if correction:
        previous = _choice_reference(correction.group(1).strip(), question)
        chosen = _choice_reference(correction.group(2).strip(), question)
        if not previous or not chosen or previous['parent_asin'] == chosen['parent_asin']:
            return None
        base = ({'kind': 'decision_tradeoff'} if question.get('kind') == 'decision_tradeoff'
                else {'slot': question['slot']})
        return {**base, **chosen, 'extra_message': None}
    # Both comparison modes use the same complete-reference rules. Politeness
    # isn't a new requirement; negation and uncertainty are not stripped away.
    text = re.sub(r'(?:,\s*|\s+)please$', '', text)
    text = re.sub(r'^i (?:prefer|care (?:more )?about)\s+', '', text)
    option = _choice_reference(text, question)
    extra = None
    if option is None:
        clauses = re.split(r'\s*,\s*(?:(?:but|and)\s+)?|\s+(?:but|and)\s+', text, maxsplit=1)
        if len(clauses) != 2:
            return None
        option = _choice_reference(clauses[0], question)
        extra = clauses[1]
    if option is None:
        return None
    base = ({'kind': 'decision_tradeoff'} if question.get('kind') == 'decision_tradeoff'
            else {'slot': question['slot']})
    return {**base, **option, 'extra_message': extra}


def declines_comparison_question(message: str, question: dict[str, Any] | None) -> bool:
    """A declined optional distinction is not a new preference or rejection."""
    if not question:
        return False
    text = message.strip().casefold().rstrip(' .!?')
    return bool(re.fullmatch(OPTION_ACCEPTANCE, text)) or text in {
                    'neither', 'neither one', 'no preference', 'no preference between them',
                    'either is fine', 'any is fine', "doesn't matter", 'not sure',
                    "i don't know", 'leave the ranking', 'leave the ranking as it is'}


def _known_conflicts(product):
    return [{'slot': signal.get('slot'), 'tier': signal.get('tier'),
             'value': signal.get('conflicting_values', signal.get('value')),
             'evidence': signal.get('evidence')}
            for signal in (product.get('match') or {}).get('signals', [])
            if signal.get('status') == 'conflict']


def _conflict_text(conflict):
    value = conflict['value']
    shown = ', '.join(map(str, value)) if isinstance(value, (list, tuple)) else str(value)
    if conflict['slot'] == 'fit_avoid':
        return f'listed as {shown}, which you preferred to avoid'
    if conflict['tier'] == 'excluded':
        return f'lists {shown}, which you excluded'
    label = str(conflict['slot']).replace('_', ' ')
    kind = 'preference' if conflict['tier'] == 'soft' else 'requirement'
    return f'conflicts with your {label} {kind} ({shown})'


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
    conflicts = {item['rank']: points for item in matches if (points := _known_conflicts(item))}
    eligible = [item for item in matches if item['rank'] not in conflicts]
    conflict_note = ' '.join(f"#{rank} is {_conflict_text(points[0])}." if points[0]['slot'] == 'fit_avoid'
                             else f"#{rank} {_conflict_text(points[0])}."
                             for rank, points in list(conflicts.items())[:3])
    if not eligible:
        return (f"I've saved {selection['value']} as a preference. The matching listings have a trade-off: "
                + conflict_note + " I wouldn't recommend one without resolving that. You can keep browsing or change a preference.",
                {'recommended_rank': None, 'verified_matches': len(matches),
                 'conflicting_ranks': list(conflicts), 'eligible_matches': 0,
                 'basis': 'verified current-page catalog facet and known conflicts'})
    pick = eligible[0]
    others = len(eligible) - 1
    message = (f"Since {selection['value']} matters to you, start with #{pick['rank']}: "
               f"{pick['title']}. Its listing explicitly supports that detail. ")
    if others:
        message += (f"{others} other displayed {'item' if others == 1 else 'items'} "
                    "also support it, so this isn't a unique winner. ")
    message += ("This is a catalog-based starting pick, not proof of quality or fit. "
                "Check the current price, size, and availability before buying.")
    if conflict_note:
        message += ' ' + conflict_note
    return message, {'recommended_rank': pick['rank'], 'parent_asin': pick['parent_asin'],
                     'verified_matches': len(matches),
                     'conflicting_ranks': list(conflicts), 'eligible_matches': len(eligible),
                     'basis': 'verified current-page catalog facet'}


def compare_preferences(
    products: list[dict[str, Any]], catalog_lookup: Callable[[str], dict[str, Any] | None],
    *, decision_help: bool = False, priorities: dict[str, float] | None = None,
    ordered_preferences: dict[str, dict[str, str]] | None = None,
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
            'known_conflicts': _known_conflicts(product),
            'first_choice_slots': sorted({signal['slot'] for signal in signals
                if signal.get('tier') == 'soft' and signal.get('status') == 'supported'
                and signal.get('slot') in (ordered_preferences or {})
                and ordered_preferences[signal['slot']]['preferred'] in signal.get('matched_values', [])}),
            'supported_slots': sorted({signal['slot'] for signal in signals
                                       if signal.get('tier') == 'soft' and signal.get('status') == 'supported'
                                       and signal.get('slot') != 'budget_target'}),
            'hard_supported': (product.get('match') or {}).get('hard_supported', 0),
            'price': _price(product.get('price')),
        })

    supported_sets = [set(row['supported_preferences']) for row in rows]
    winner = None
    winner_basis = None
    if len(rows) >= 2:
        leaders = [index for index, signals in enumerate(supported_sets)
                   if signals and all(signals > other for j, other in enumerate(supported_sets) if j != index)
                   and all(rows[index]['hard_supported'] >= other['hard_supported']
                           for j, other in enumerate(rows) if j != index)]
        if len(leaders) == 1:
            winner = rows[leaders[0]]['rank']
            winner_basis = 'supported_preferences'

    # Explicit priorities are tiers, not extra votes for several weak signals.
    # No recency or relevance score is interpreted as a shopper's priority.
    slots = {slot for row in rows for slot in row['supported_slots']}
    priority_values = {slot: (priorities or {}).get(slot, 1.0) for slot in slots}
    levels = sorted({value for value in priority_values.values()
                     if isinstance(value, (int, float)) and not isinstance(value, bool)
                     and math.isfinite(value) and value > 0}, reverse=True)
    priority_scores = {}
    if winner is None and len(levels) > 1:
        priority_scores = {row['parent_asin']: [sum(priority_values[slot] == level
                                                   for slot in row['supported_slots'])
                                               for level in levels] for row in rows}
        leaders = [row for row in rows if all(
            priority_scores[row['parent_asin']] > priority_scores[other['parent_asin']]
            and row['hard_supported'] >= other['hard_supported']
            for other in rows if other is not row)]
        if len(leaders) == 1:
            winner = leaders[0]['rank']
            winner_basis = 'explicit_priority'

    if winner is None and ordered_preferences and len(rows) >= 2:
        leaders = [row for row in rows if row['first_choice_slots'] and all(
            set(row['first_choice_slots']) > set(other['first_choice_slots'])
            and set(row['supported_slots']) >= set(other['supported_slots'])
            and row['hard_supported'] >= other['hard_supported']
            for other in rows if other is not row)]
        if len(leaders) == 1:
            winner = leaders[0]['rank']
            winner_basis = 'first_choice'

    blocked_recommendation = next((row for row in rows if row['rank'] == winner and row['known_conflicts']), None)
    if blocked_recommendation:
        winner = None
        winner_basis = None

    decision_slots = []
    if winner_basis in {'explicit_priority', 'first_choice'}:
        chosen = next(row for row in rows if row['rank'] == winner)
        others = [row for row in rows if row is not chosen]
        if winner_basis == 'first_choice':
            decision_slots = sorted(set().union(*(
                set(chosen['first_choice_slots']) - set(other['first_choice_slots']) for other in others)))
        else:
            distinguishing_slots = set()
            for other in others:
                index = next(index for index, (left, right) in enumerate(zip(
                    priority_scores[chosen['parent_asin']], priority_scores[other['parent_asin']]))
                    if left != right)
                distinguishing_slots.update(slot for slot in
                    set(chosen['supported_slots']) - set(other['supported_slots'])
                    if priority_values[slot] == levels[index])
            decision_slots = sorted(distinguishing_slots)

    if winner is None:
        opening = ("I wouldn't call one a clear winner from these listings alone. Here's what I can verify:"
                   if decision_help else
                   "I can't confidently choose one from these listings alone. Let's look at the differences.")
    elif winner_basis == 'first_choice':
        preferred = ', '.join(str(ordered_preferences[slot]['preferred']) for slot in decision_slots)
        opening = (f"I'd start with #{winner}: its listing supports your first-choice preference for {preferred}. "
                   "Your fallback is still an option; this isn't a guarantee of quality or fit.")
    elif winner_basis == 'explicit_priority':
        labels = ', '.join({'style': 'fit', 'material': 'fabric'}.get(slot, slot.replace('_', ' '))
                           for slot in decision_slots)
        opening = (f"I'd start with #{winner}: its listing supports your higher-priority preferences for {labels}. "
                   "That's based on the details listed, not a guarantee of quality or fit.")
    else:
        opening = (f"I'd start with #{winner}: its listing matches more of what you asked for. "
                   "That makes it a closer match, not a guarantee of quality or fit.")

    shared_facets = {key: value for key, value in rows[0]['verified_facets'].items()
                     if all(row['verified_facets'].get(key) == value for row in rows[1:])}
    shared_preferences = set.intersection(*(set(row['supported_preferences']) for row in rows))
    prices = [row['price'] for row in rows]
    shared_price = prices[0] if all(price is not None and price == prices[0] for price in prices) else None
    descriptions = []
    for row in rows:
        distinct_preferences = [value for value in row['supported_preferences']
                                if value not in shared_preferences]
        details = list(dict.fromkeys(value for key, value in row['verified_facets'].items()
                                     if key not in shared_facets and value not in distinct_preferences))
        if distinct_preferences:
            details.append('matches your ' + ', '.join(distinct_preferences) + ' preference')
        for conflict in row['known_conflicts']:
            details.append(_conflict_text(conflict))
        if row['price'] is not None and shared_price is None:
            details.append(f"catalog price ${row['price']:g}")
        if details:
            descriptions.append(f"#{row['rank']}: " + '; '.join(details))
    shared_details = list(dict.fromkeys(value for value in shared_facets.values()
                                       if value not in shared_preferences))
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
    has_conflicts = any(row['known_conflicts'] for row in rows)
    next_question = _distinguishing_question(rows) if winner is None and not has_conflicts else None
    message = opening + '\n\n' + '\n'.join(descriptions)
    if price_note:
        message += '\n' + price_note
    if next_question:
        message += '\n\n' + next_question['message']
    elif decision_help and winner is None and not has_conflicts and len(rows) >= 3:
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
                     'winner_basis': winner_basis, 'priority_levels': levels,
                     'decision_slots': decision_slots,
                     'blocked_recommendation_rank': blocked_recommendation['rank'] if blocked_recommendation else None,
                     'priority_scores': priority_scores,
                     'next_question': next_question['message'] if next_question else None,
                     'question': next_question,
                     'basis': 'displayed listing facts and current-session preferences only'}
