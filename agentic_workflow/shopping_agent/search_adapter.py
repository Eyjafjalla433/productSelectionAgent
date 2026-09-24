"""Translate versioned workflow requests to the unchanged search_tool API."""
from copy import deepcopy
from dataclasses import replace
import math
import sys

from techjam_agent.contracts import Candidate, PRODUCT_FIELDS, RankedCandidate
from techjam_agent.contracts_v2 import RetrievalResultV2, RetrievalStats, RankingResultV2
from .retrieval import requirements_from_state, StateAwareRetriever


class SearchToolAdapter:
    mode = 'search_tool'

    def __init__(self, search_function=None, details_function=None):
        if (search_function is None) != (details_function is None):
            raise ValueError('Supply both search_function and details_function')
        self.search_function = search_function
        self.details_function = details_function
        self.products = {}
        self.scores = {}

    def _functions(self):
        if self.search_function is None:
            # Importing must not create __pycache__ inside the read-only tool.
            previous = sys.dont_write_bytecode
            try:
                sys.dont_write_bytecode = True
                from search_tool import tool
                from agentic_workflow.search_resources import model_directory
                tool.MODEL_DIR = model_directory()
                search_products, get_product_details = tool.search_products, tool.get_product_details
            finally:
                sys.dont_write_bytecode = previous
            return search_products, get_product_details
        return self.search_function, self.details_function

    def generate(self, request):
        state = request.state
        requirements = requirements_from_state(state, relax_soft=request.relax_soft)
        query = ' '.join((requirements.category, *requirements.hard_constraints,
                          *requirements.soft_preferences)).strip() or state.query.strip()
        search_products, get_product_details = self._functions()
        results = search_products(query, request.candidate_limit)
        if not isinstance(results, list) or len(results) > request.candidate_limit:
            raise ValueError('search_tool returned an invalid result list')
        ids, scores = [], {}
        for row in results:
            product_id = row.get('product_id')
            score = float(row['score'])
            if not isinstance(product_id, str) or not product_id or product_id in scores or not math.isfinite(score):
                raise ValueError('search_tool returned invalid IDs or scores')
            ids.append(product_id)
            scores[product_id] = score
        details = get_product_details(ids) if ids else []
        by_id = {}
        for row in details:
            product_id = row.get('product_id')
            if product_id not in scores or product_id in by_id:
                raise ValueError('search_tool returned unexpected detail IDs')
            by_id[product_id] = row
        candidates = []
        for product_id in ids:
            row = by_id.get(product_id, {})
            if not row.get('found'):
                continue
            product = {
                'parent_asin': product_id, 'title': row.get('title', ''),
                'store': row.get('brand', ''), 'categories': [],
                'features': [row.get('bullet_point', '')], 'description': [],
                'details': {'Brand': row.get('brand', ''), 'Color': row.get('color', '')},
                'price': None, 'average_rating': None, 'rating_number': None,
                'product_url': row.get('product_url', ''), 'url_verified': False,
            }
            self.products[product_id] = product
            self.scores[product_id] = scores[product_id]
            if product_id in state.rejected_asins:
                continue
            if not StateAwareRetriever.satisfies(self, product_id, state):
                continue
            candidates.append(Candidate(product_id, len(candidates) + 1,
                {'search_tool': ids.index(product_id) + 1},
                {field: deepcopy(product.get(field)) for field in PRODUCT_FIELDS}))
        unseen = [candidate for candidate in candidates if candidate.parent_asin not in state.shown_asins]
        if unseen:
            candidates = [replace(candidate, candidate_rank=index)
                          for index, candidate in enumerate(unseen, 1)]
        return RetrievalResultV2(
            candidate_set_id=f'{state.session_id}:{state.turn}:v{state.state_version}:a{request.attempt}',
            session_id=state.session_id, turn=state.turn, state_version=state.state_version,
            candidate_limit=request.candidate_limit, candidates=tuple(candidates),
            stats=RetrievalStats(), state_snapshot=state.to_dict(),
            legacy_requirements=requirements,
            warnings=('search_tool ranking retained; price, inventory and variants are unknown.',))

    def rerank(self, retrieval, *, top_k):
        rows = tuple(RankedCandidate(c.parent_asin, index,
            self.scores[c.parent_asin], ('search_tool original score and order',))
            for index, c in enumerate(retrieval.candidates[:top_k], 1))
        result = RankingResultV2(retrieval.candidate_set_id, retrieval.session_id,
            retrieval.turn, retrieval.state_version, rows, 'search_tool', score_semantics='uncalibrated')
        result.validate_against(retrieval, top_k=top_k)
        return result
