"""Ground a 'more like this' request in one explicitly chosen catalog facet."""

from __future__ import annotations

import re
import json

from shopping_agent.retrieval import product_colors, pure_cotton_matches, style_evidence


FIT_VALUES = ('slim fit', 'loose fit', 'regular fit')
COLOR_VALUES = ('black', 'white', 'blue', 'red', 'green', 'pink', 'purple', 'brown',
                'gray', 'yellow', 'orange', 'beige')


def supported_facets(product: dict) -> dict[str, dict[str, str]]:
    """Return only unambiguous facts from this catalog variant."""
    result: dict[str, dict[str, str]] = {}
    fits = [(value, style_evidence(product, value)) for value in FIT_VALUES]
    fits = [(value, evidence) for value, evidence in fits if evidence]
    if len(fits) == 1:
        value, evidence = fits[0]
        result['fit'] = {'slot': 'style', 'value': value, 'evidence': evidence[:240]}

    colors = product_colors(product)
    # Navy is a subset of blue in the current catalog adapter. Do not treat
    # that pair as two conflicting colors, but avoid multi-color guesses.
    if 'navy' in colors and 'blue' in colors:
        colors = (colors - {'navy'})
    colors = colors & set(COLOR_VALUES)
    if len(colors) == 1:
        value = next(iter(colors))
        source = str(product.get('color') or product.get('title') or '')
        result['color'] = {'slot': 'color', 'value': value, 'evidence': source[:240]}

    if pure_cotton_matches(product):
        source = ' '.join(str(product.get(key) or '') for key in ('title', 'features', 'description'))
        result['fabric'] = {'slot': 'material', 'value': '100% cotton', 'evidence': source[:240]}
    return result


def facets_not_in_preferences(facets: dict[str, dict[str, str]], state: dict) -> dict[str, dict[str, str]]:
    """Only ask about evidence that has not already been saved as a preference."""
    remaining = {}
    for key, facet in facets.items():
        slot, value = facet['slot'], facet['value']
        known = [json.loads(raw) for tier in ('hard', 'soft')
                 if (raw := state.get(tier, {}).get(slot)) is not None]
        known_values = [entry for raw in known for entry in (raw if isinstance(raw, list) else [raw])]
        if value not in known_values:
            remaining[key] = facet
    return remaining


def parse_facet_reply(message: str, facets: dict[str, dict[str, str]]) -> tuple[tuple[str, ...], str | None] | None:
    """Accept chosen facets plus independently stated shopping details."""
    text = message.strip().casefold().rstrip('.!?')
    if re.search(r"\b(?:don't|do not|not|except|neither)\b", text):
        return None
    parts = [part.strip() for part in re.split(r'\s*(?:,|\band\b|\bbut\b)\s*', text)]
    aliases = {'fit': 'fit', 'cut': 'fit', 'color': 'color', 'colour': 'color',
               'fabric': 'fabric', 'material': 'fabric'}
    chosen = []
    extra = []
    for part in parts:
        normalized = re.sub(r'^(?:i\s+(?:(?:really|especially)\s+)?(?:like|prefer|mean|want)\s+)', '', part)
        normalized = re.sub(r'^(?:(?:the|same|its|their)\s+)+', '', normalized).strip()
        if normalized in {'all', 'all of them', 'all three', 'everything'} or (
            normalized in {'both', 'both of them', 'both of those'} and len(facets) == 2
        ):
            chosen.extend(key for key in facets if key not in chosen)
            continue
        key = aliases.get(normalized)
        if key is None:
            key = next((candidate for candidate, facet in facets.items()
                        if normalized in {facet['value'],
                                          f"{facet['value']} {candidate}",
                                          f"{facet['value']} colour"}), None)
        if key in facets and key not in chosen:
            chosen.append(key)
        elif key is None:
            extra.append(part)
        else:
            return None
    if not chosen:
        return None
    if extra:
        from intent_router.turn_router import TurnIntentRouter
        extra_message = ', '.join(extra)
        parsed = TurnIntentRouter().understand_turn(extra_message)
        if (not parsed.slot_updates or
                any(update.slot == 'category' for update in parsed.slot_updates) or
                parsed.decision_evidence.get('requirement_control')):
            return None
        return tuple(chosen), extra_message
    return tuple(chosen), None


def repair_facet_reply(message: str, facets: dict[str, dict[str, str]]) -> tuple[str, str | None] | None:
    """Recognize only narrow, recoverable answers to the current facet question."""
    text = message.strip().casefold().rstrip('.!?')
    text = re.sub(r'^(?:i\s+(?:like|mean|want)\s+)', '', text)
    if len(facets) > 2 and text in {'both', 'both of them', 'both of those'}:
        return 'ambiguous_pair', None
    negative = re.fullmatch(r"(?:i\s+(?:don't|do not)\s+(?:like|want)\s+|not\s+)(?:(?:the|its)\s+)?(fit|cut|color|colour|fabric|material)", text)
    if negative:
        aliases = {'cut': 'fit', 'colour': 'color', 'material': 'fabric'}
        excluded = aliases.get(negative.group(1), negative.group(1))
        if excluded in facets:
            return 'excluded_facet', excluded
    return None


def requirement_from_facets(facets: dict[str, dict[str, str]], chosen: tuple[str, ...], extra: str | None = None) -> str:
    return ', '.join([*(f"I prefer {facets[key]['value']}" for key in chosen),
                      *([extra] if extra else []), 'show me more'])
