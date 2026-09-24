"""Small deterministic parser for disclosed product requirements."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
FIELD_LABEL_RE = re.compile(r"\b([a-z_]+)\s*:", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
}
KNOWN_FIELD_LABELS = frozenset(
    {
        "brand", "budget", "category", "categories", "color", "colors",
        "feature", "material", "size", "style", "use_case",
    }
)


@dataclass(frozen=True)
class ParsedText:
    retrieval_terms: tuple[str, ...]
    field_labels: tuple[str, ...]


@dataclass(frozen=True)
class StructuredIntent:
    category_terms: tuple[str, ...]
    hard_terms: tuple[str, ...]
    soft_terms: tuple[str, ...]
    all_terms: tuple[str, ...]


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def tokenize(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    ]


def parse_text(text: str) -> ParsedText:
    field_labels = _unique(
        label.lower()
        for label in FIELD_LABEL_RE.findall(text)
        if label.lower() in KNOWN_FIELD_LABELS
    )
    return ParsedText(
        retrieval_terms=tuple(term for term in tokenize(text) if term not in field_labels),
        field_labels=field_labels,
    )


def _parse_values(values: Iterable[object]) -> tuple[str, ...]:
    return _unique(
        term
        for value in values
        for term in parse_text(str(value)).retrieval_terms
    )


def parse_intent(
    category: str,
    hard_constraints: Iterable[object],
    soft_preferences: Iterable[object],
) -> StructuredIntent:
    category_terms = _unique(parse_text(category).retrieval_terms)
    hard_terms = _parse_values(hard_constraints)
    soft_terms = _parse_values(soft_preferences)
    return StructuredIntent(
        category_terms=category_terms,
        hard_terms=hard_terms,
        soft_terms=soft_terms,
        all_terms=_unique((*category_terms, *hard_terms, *soft_terms)),
    )
