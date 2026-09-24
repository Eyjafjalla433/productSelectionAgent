"""Locked Lite and Dense Top-50 candidate generators."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from math import log1p
from pathlib import Path
from typing import Any

from techjam_agent.contracts import Candidate, CandidateSet, Requirements
from techjam_agent.query import StructuredIntent, parse_intent, parse_text, tokenize


ROUTE_DEPTH = 100
FIXED_SCHEDULE = (
    "color_normalized_full_and",
    "category_gate",
    "category_or",
    "color_normalized_full_and",
    "color_normalized_full_and",
    "color_normalized_full_and",
)
INDEX_FIELDS = ("title", "categories", "features", "details", "store", "description")
PRODUCT_FIELDS = ("title", "categories", "features", "details", "description", "store")
VISIBLE_FIELDS = PRODUCT_FIELDS
CATEGORY_ROOT_TERMS = frozenset({"clothing", "shoes", "jewelry"})


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _or_expression(terms: Iterable[str]) -> str:
    return " OR ".join(f'"{term}"' for term in _unique(terms))


def _and_expression(terms: Iterable[str]) -> str:
    return " AND ".join(f'"{term}"' for term in _unique(terms))


def _route_expressions(plan: StructuredIntent, full_query: str) -> dict[str, str]:
    query_terms = _unique(tokenize(full_query))[:40]
    color_labels = set(parse_text(full_query).field_labels) & {"color", "colors"}
    color_normalized = tuple(term for term in query_terms if term not in color_labels)
    category_expression = _and_expression(plan.category_terms)
    optional_expression = _or_expression((*plan.hard_terms, *plan.soft_terms))
    category_gate = (
        f"{category_expression} AND ({optional_expression})"
        if category_expression and optional_expression
        else category_expression or optional_expression
    )
    return {
        "color_normalized_full_and": _and_expression(color_normalized),
        "category_gate": category_gate,
        "category_or": _or_expression(plan.category_terms),
    }


def build_retrieval_plan(requirements: Requirements) -> dict[str, object]:
    full_query = " ".join(
        value
        for value in (
            requirements.category,
            *requirements.hard_constraints,
            *requirements.soft_preferences,
        )
        if value
    )
    plan = parse_intent(
        requirements.category,
        requirements.hard_constraints,
        requirements.soft_preferences,
    )
    return {"full_query": full_query, "expressions": _route_expressions(plan, full_query)}


def _next_unseen(
    rankings: Mapping[str, list[str]],
    route_name: str,
    cursors: dict[str, int],
    seen: set[str],
) -> str | None:
    ranking = rankings[route_name]
    cursor = cursors[route_name]
    while cursor < len(ranking):
        parent_asin = ranking[cursor]
        cursor += 1
        cursors[route_name] = cursor
        if parent_asin not in seen:
            return parent_asin
    return None


def select_by_schedule(
    rankings: Mapping[str, list[str]], schedule: tuple[str, ...], capacity: int
) -> list[str]:
    cursors = {route: 0 for route in rankings}
    selected: list[str] = []
    seen: set[str] = set()
    while len(selected) < capacity:
        added = False
        for route_name in schedule:
            parent_asin = _next_unseen(rankings, route_name, cursors, seen)
            if parent_asin is None:
                continue
            selected.append(parent_asin)
            seen.add(parent_asin)
            added = True
            if len(selected) == capacity:
                return selected
        if not added:
            return selected
    return selected


def evidence_category_terms(category: str) -> tuple[str, ...]:
    return tuple(term for term in tokenize(category) if term not in CATEGORY_ROOT_TERMS)


def _disclosed_phrases(values: Iterable[object]) -> tuple[str, ...]:
    phrases: list[str] = []
    for value in values:
        terms = tokenize(str(value))
        if len(terms) >= 3:
            phrase = " ".join(terms)
            if phrase not in phrases:
                phrases.append(phrase)
    return tuple(phrases)


class CatalogEvidenceRanker:
    def __init__(self, products: dict[str, dict[str, Any]]) -> None:
        self.products = products
        self.catalog_tokens = {
            parent_asin: set(
                tokenize(" ".join(_text(product.get(field)) for field in VISIBLE_FIELDS))
            )
            for parent_asin, product in products.items()
        }
        self.category_tokens = {
            parent_asin: set(tokenize(_text(product.get("categories"))))
            for parent_asin, product in products.items()
        }
        self.catalog_text = {
            parent_asin: " ".join(
                tokenize(" ".join(_text(product.get(field)) for field in VISIBLE_FIELDS))
            )
            for parent_asin, product in products.items()
        }
        self.postings: dict[str, set[str]] = defaultdict(set)
        self.category_postings: dict[str, set[str]] = defaultdict(set)
        for parent_asin, terms in self.catalog_tokens.items():
            for term in terms:
                self.postings[term].add(parent_asin)
            for term in self.category_tokens[parent_asin]:
                self.category_postings[term].add(parent_asin)

    def rank(self, requirements: Requirements, *, limit: int) -> list[str]:
        plan = parse_intent(
            requirements.category,
            requirements.hard_constraints,
            requirements.soft_preferences,
        )
        category_terms = set(evidence_category_terms(requirements.category))
        hard_terms = set(plan.hard_terms)
        soft_terms = set(plan.soft_terms)
        phrases = _disclosed_phrases(requirements.soft_preferences)
        if hard_terms:
            posting_lists = [self.postings.get(term, set()) for term in hard_terms]
            candidate_ids = set.intersection(*posting_lists) if posting_lists else set()
        else:
            candidate_ids = set(self.products)
        if category_terms:
            category_ids = set().union(
                *(self.category_postings.get(term, set()) for term in category_terms)
            )
            candidate_ids &= category_ids
        ranked: list[tuple[tuple[float, ...], str]] = []
        for parent_asin in candidate_ids:
            product = self.products[parent_asin]
            terms = self.catalog_tokens[parent_asin]
            category_hits = len(category_terms & self.category_tokens[parent_asin])
            hard_hits = len(hard_terms & terms)
            soft_hits = len(soft_terms & terms)
            phrase_hits = sum(phrase in self.catalog_text[parent_asin] for phrase in phrases)
            score = (
                float(phrase_hits),
                hard_hits / max(1, len(hard_terms)),
                category_hits / max(1, len(category_terms)),
                soft_hits / max(1, len(soft_terms)),
                log1p(float(product.get("rating_number") or 0)),
            )
            ranked.append((score, parent_asin))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [parent_asin for _, parent_asin in ranked[:limit]]


class CatalogFtsIndex:
    def __init__(self, catalog_path: str | Path) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.products: dict[str, dict[str, Any]] = {}
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, store, description, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        batch: list[tuple[str, ...]] = []
        with Path(catalog_path).open(encoding="utf-8") as handle:
            for line in handle:
                product = json.loads(line)
                parent_asin = str(product["parent_asin"])
                self.products[parent_asin] = product
                batch.append(
                    (parent_asin, *(_text(product.get(field)) for field in INDEX_FIELDS))
                )
                if len(batch) == 1000:
                    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()

    def rank(self, expression: str, *, depth: int) -> list[str]:
        if not expression:
            return []
        rows = self.connection.execute(
            "SELECT parent_asin FROM products WHERE products MATCH ? "
            "ORDER BY bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?",
            (expression, depth),
        )
        return [str(row[0]) for row in rows]


def _append_unseen(selected: list[str], ranking: Sequence[str], capacity: int) -> None:
    if len(selected) >= capacity:
        return
    seen = set(selected)
    for parent_asin in ranking:
        if parent_asin in seen:
            continue
        selected.append(parent_asin)
        seen.add(parent_asin)
        if len(selected) >= capacity:
            return


class LiteTop50CandidateGenerator:
    """Standard-library approximation with lexical fallback instead of Dense."""

    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        self.fts = CatalogFtsIndex(catalog_path)
        self.evidence = CatalogEvidenceRanker(self.fts.products)

    def generate(
        self,
        requirements: Requirements,
        *,
        session_id: str,
        turn: int,
    ) -> CandidateSet:
        plan = build_retrieval_plan(requirements)
        expressions = plan["expressions"]
        assert isinstance(expressions, dict)
        route_rankings = {
            name: self.fts.rank(str(expression), depth=ROUTE_DEPTH)
            for name, expression in expressions.items()
        }
        selected = select_by_schedule(route_rankings, FIXED_SCHEDULE, 30)
        evidence_ranking = self.evidence.rank(requirements, limit=100)
        _append_unseen(selected, evidence_ranking, 50)
        if len(selected) < 50:
            for ranking in route_rankings.values():
                _append_unseen(selected, ranking, 50)
        if len(selected) < 50:
            _append_unseen(selected, list(self.fts.products), 50)
        if len(selected) != 50:
            raise ValueError("catalog does not contain 50 unique products")

        allowed = set(selected)
        source_ranks: dict[str, dict[str, int]] = {value: {} for value in selected}
        for source_name, ranking in {**route_rankings, "evidence": evidence_ranking}.items():
            for rank, parent_asin in enumerate(ranking, start=1):
                if parent_asin in allowed:
                    source_ranks[parent_asin][source_name] = rank
        candidates = tuple(
            Candidate(
                parent_asin=parent_asin,
                candidate_rank=rank,
                source_ranks=source_ranks[parent_asin],
                product={field: self.fts.products[parent_asin].get(field) for field in PRODUCT_FIELDS},
            )
            for rank, parent_asin in enumerate(selected, start=1)
        )
        return CandidateSet(
            candidate_set_id=f"{session_id}:{turn}",
            session_id=session_id,
            turn=turn,
            requirements=requirements,
            candidates=candidates,
        )


class ExactDenseTop50CandidateGenerator(LiteTop50CandidateGenerator):
    """Exact locked Top-50: evidence-preserved pool with BGE Dense padding."""

    MODEL_NAME = "BAAI/bge-small-en-v1.5"
    QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

    def __init__(
        self,
        catalog_path: str | Path = "data/catalog.jsonl",
        *,
        resources_dir: str | Path | None = None,
    ) -> None:
        super().__init__(catalog_path)
        import numpy as np
        from sentence_transformers import SentenceTransformer

        if resources_dir is None:
            configured = os.environ.get("TECHJAM_RESOURCES_DIR")
            resources_dir = configured or Path(__file__).resolve().parents[1] / "resources"
        resources = Path(resources_dir)
        embeddings_dir = resources / "dense_catalog_embeddings"
        stem = "baai-bge-small-en-v1-5"
        manifest = json.loads(
            (embeddings_dir / f"{stem}-manifest.json").read_text(encoding="utf-8")
        )
        if (
            manifest.get("model_name") != self.MODEL_NAME
            or not manifest.get("normalized_embeddings")
        ):
            raise ValueError("Dense resource manifest differs from the locked model")
        self.document_ids = [
            str(value)
            for value in json.loads(
                (embeddings_dir / f"{stem}-parent-asins.json").read_text(encoding="utf-8")
            )
        ]
        self.document_embeddings = np.load(
            embeddings_dir / f"{stem}-catalog-embeddings.npy"
        )
        if self.document_ids != list(self.fts.products):
            raise ValueError("Dense resource product order differs from catalog")
        self.np = np
        self.model = SentenceTransformer(
            str(resources / "bge-small-en-v1.5"),
            device="cpu",
            local_files_only=True,
        )
        self.model.max_seq_length = 256

    def _dense_ranking(self, query: str, *, depth: int = 100) -> list[str]:
        encoded = self.model.encode_query(
            [self.QUERY_PREFIX + query],
            batch_size=1,
            show_progress_bar=False,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        query_embedding = self.np.asarray(encoded[0], dtype=self.np.float32)
        scores = self.document_embeddings @ query_embedding
        indices = self.np.argpartition(-scores, kth=depth - 1)[:depth]
        ordered = indices[self.np.argsort(-scores[indices], kind="stable")]
        return [self.document_ids[int(index)] for index in ordered]

    def generate(
        self,
        requirements: Requirements,
        *,
        session_id: str,
        turn: int,
    ) -> CandidateSet:
        plan = build_retrieval_plan(requirements)
        expressions = plan["expressions"]
        assert isinstance(expressions, dict)
        route_rankings = {
            name: self.fts.rank(str(expression), depth=ROUTE_DEPTH)
            for name, expression in expressions.items()
        }
        base = select_by_schedule(route_rankings, FIXED_SCHEDULE, 30)
        evidence_ranking = self.evidence.rank(requirements, limit=100)
        selected = list(base)
        _append_unseen(selected, evidence_ranking, 50)
        if len(selected) < 50:
            dense_hybrid = list(base)
            dense_ranking = self._dense_ranking(str(plan["full_query"]))
            _append_unseen(dense_hybrid, dense_ranking, 50)
            _append_unseen(selected, dense_hybrid, 50)
        if len(selected) != 50:
            raise ValueError("Dense fallback could not produce 50 unique products")

        allowed = set(selected)
        source_ranks: dict[str, dict[str, int]] = {value: {} for value in selected}
        for source_name, ranking in {**route_rankings, "evidence": evidence_ranking}.items():
            for rank, parent_asin in enumerate(ranking, start=1):
                if parent_asin in allowed:
                    source_ranks[parent_asin][source_name] = rank
        return CandidateSet(
            candidate_set_id=f"{session_id}:{turn}",
            session_id=session_id,
            turn=turn,
            requirements=requirements,
            candidates=tuple(
                Candidate(
                    parent_asin=parent_asin,
                    candidate_rank=rank,
                    source_ranks=source_ranks[parent_asin],
                    product={
                        field: self.fts.products[parent_asin].get(field)
                        for field in PRODUCT_FIELDS
                    },
                )
                for rank, parent_asin in enumerate(selected, start=1)
            ),
        )
