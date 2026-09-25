"""Small, read-only apparel terminology guide for the shopping conversation."""

from __future__ import annotations

import re


FIT_TERMS = {
    'slim fit': 'a closer cut through the body than regular fit',
    'regular fit': 'a conventional cut with some room, between slim and loose fits',
    'loose fit': 'a roomier cut than regular fit',
    'relaxed fit': 'a roomier cut than regular fit',
    'oversized': 'an intentionally larger-looking cut',
}
FABRIC_TERMS = {
    'fabric': 'the material a garment is made from, such as cotton or polyester',
    'material': 'the fabric or fibers a garment is made from',
    'cotton': 'a plant-derived fiber; a listing may use it alone or in a blend',
    'polyester': 'a synthetic fiber; a listing may use it alone or in a blend',
    'linen': 'a plant-derived fiber made from flax',
    'wool': 'an animal-derived fiber; the exact blend and care needs vary by item',
}


def explain_pending_question(message: str, pending: dict | None,
                             *, include_followup: bool = True) -> str | None:
    """Explain an open question from its actual scope and catalog evidence."""
    if not pending:
        return None
    text = message.strip().casefold().rstrip(' .!?')
    if '?' in text:
        return None  # A following request belongs to the compound-turn path.
    match = (re.fullmatch(r'why (?:are you asking|do you ask)(?: me)?(?: about (?P<topic>.+))?', text)
             or re.fullmatch(r'why do you need to know (?P<topic>.+)', text)
             or re.fullmatch(r'why did you ask(?: me)?(?: that| this| about (?P<topic>.+))', text))
    if not match:
        return None
    labels = {'color': 'color', 'material': 'fabric', 'style': 'fit',
              'use_case': 'intended use', 'category': 'product type', 'budget': 'price limit'}
    aliases = {'color': 'color', 'colour': 'color', 'fabric': 'material', 'material': 'material',
               'fit': 'style', 'style': 'style', 'use': 'use_case',
               'category': 'category', 'product type': 'category',
               'price': 'budget', 'budget': 'budget'}
    target = pending.get('target_slot')
    if target not in labels:
        return None
    topic = (match.groupdict().get('topic') or '').strip()
    topic = re.sub(r'^(?:my|the|a)\s+', '', topic)
    if topic and topic not in aliases:
        return (f"I wasn't asking for {topic}. The open question is about {labels[target]}; " +
                ("you can answer it or keep browsing." if include_followup else
                 "I haven't changed your preferences."))
    if topic and aliases.get(topic) not in {target, None}:
        return (f"I'm asking about {labels[target]}, not {topic}. " +
                ("You can answer that question or tell me what you'd rather focus on."
                 if include_followup else "I haven't changed your preferences."))
    if target == 'category':
        if pending.get('reason') == 'choose_category_alternative':
            return ("You mentioned more than one product type. This search covers one type at a time, "
                    "so I asked which to explore first; we can switch later." +
                    (" You can still answer the open choice." if include_followup else ""))
        return ("I need a product type before I can search this catalog. You can give me several details "
                "at once—type, color, size, or budget—and I'll keep them together.")
    if target == 'budget':
        return ("These listings don't have prices, so I can't verify a strict budget match. "
                "I asked whether you'd rather see unpriced ideas with the budget kept as a preference, "
                "or keep the strict limit." +
                (" Your choice is still open." if include_followup else ""))
    options = [pending.get('option_labels', {}).get(value, value)
               for value in pending.get('options', ())]
    options = [str(value) for value in options[:3]]
    if len(options) < 2 or not pending.get('evidence'):
        return None
    named = (' and '.join(options) if len(options) == 2 else
             ', '.join(options[:-1]) + ', and ' + options[-1])
    return (f"I asked because the retrieved listings include {named}; knowing which you lean toward "
            "could help me prioritize them. It's optional—the results stay visible" +
            (", and you can answer the open choice or keep browsing." if include_followup else "."))


def explain_shopping_term(message: str, pending: dict | None,
                          *, include_followup: bool = True) -> str | None:
    """Answer a narrow terminology question without accepting a preference."""
    pending = pending or {}
    target = pending.get('target_slot')
    text = message.strip().casefold().rstrip(' .!?')
    glossaries = ((FIT_TERMS,) if target == 'style' else
                  (FABRIC_TERMS,) if target == 'material' else
                  (FIT_TERMS, FABRIC_TERMS))
    options = {str(value).casefold() for value in pending.get('options', ())}
    closing = ((" I haven't changed your preferences. You can still choose an option "
                "or say Show me first." if pending else
                " I haven't changed your preferences. You can keep browsing or tell me what matters to you.")
               if include_followup else '')
    comparison = re.fullmatch(
        r"what(?:'s| is) the difference between (.+?) and (.+)", text)
    if comparison:
        first, second = (part.strip(' "\'') for part in comparison.groups())
        glossary = next((item for item in glossaries if first in item and second in item), None)
        if glossary and (target not in {'style', 'material'} or
                         first in options and second in options):
            answer = (f"{first.capitalize()} generally means {glossary[first]}; "
                      f"{second} generally means {glossary[second]}.")
            caveat = (" Brands use fit labels differently, so check each size chart."
                      if glossary is FIT_TERMS else
                      " Check each listing's fiber percentages for the exact composition.")
            return answer + caveat + closing
    match = re.fullmatch(
        r'(?:what (?:does|do) (?P<meaning>.+?) mean|'
        r'what do you mean by (?P<clarification>.+?)|'
        r'what is (?P<definition>.+?))', text)
    if not match:
        return None
    term = next(value for value in match.groupdict().values() if value).strip(' "\'')
    glossary = next((item for item in glossaries if term in item), None)
    if not glossary or (target in {'style', 'material'} and
                       term not in options and term not in {'fabric', 'material'}):
        return None
    answer = f"{term.capitalize()} generally means {glossary[term]}."
    if glossary is FIT_TERMS:
        answer += " Brands use fit labels differently, so check the item's measurements and size chart."
    else:
        answer += " Check the listing's fiber percentages if the exact composition matters."
    return answer + closing


def split_term_question_and_request(message: str, pending: dict | None) -> tuple[str, str] | None:
    """Keep a leading terminology question separate from a following request."""
    match = re.fullmatch(r'(?P<question>[^?]{1,160}\?)\s+(?P<request>.+)', message.strip(), re.S)
    if not match:
        return None
    answer = (explain_shopping_term(match.group('question'), pending, include_followup=False)
              or explain_pending_question(match.group('question'), pending, include_followup=False))
    request = match.group('request').strip()
    return (answer, request) if answer and request else None
