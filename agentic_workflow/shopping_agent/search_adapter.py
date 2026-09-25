"""Translate versioned workflow requests to the unchanged search_tool API."""
from copy import deepcopy
from dataclasses import replace
import math
import sys
import importlib

from techjam_agent.contracts import Candidate, PRODUCT_FIELDS, RankedCandidate
from techjam_agent.contracts_v2 import RetrievalResultV2, RetrievalStats, RankingResultV2
from .retrieval import (requirements_from_state, StateAwareRetriever, color_matches,
                        material_matches, size_matches, style_matches, breathable_matches)


def _text_list(value):
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [text for text in value if isinstance(text, str) and text.strip()] if isinstance(value, list) else []


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
                from agentic_workflow.search_resources import model_directory, configure_allocator
                configure_allocator()
                tool = importlib.import_module('search_tool.tool')
                tool.MODEL_DIR = model_directory()
                search_products, get_product_details = tool.search_products, tool.get_product_details
            finally:
                sys.dont_write_bytecode = previous
            return search_products, get_product_details
        return self.search_function, self.details_function

    def generate(self, request):
        state = request.state
        colors = state.soft_preferences.get('color', ())
        ordered_color = (len(colors) == 2 and colors[0].weight > colors[1].weight)
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
            product_details = deepcopy(row.get('details')) if isinstance(row.get('details'), dict) else {}
            for field, key in (('Brand', 'brand'), ('Color', 'color')):
                if row.get(key):
                    product_details[field] = row[key]
            bullets = (_text_list(row.get('product_bullet_points')) or
                       _text_list(row.get('features')) or _text_list(row.get('bullet_point')))
            descriptions = (_text_list(row.get('product_description')) or
                            _text_list(row.get('description')))
            product = {
                'parent_asin': product_id, 'title': row.get('title', ''),
                'store': row.get('brand', ''), 'categories': [],
                'features': bullets, 'description': descriptions,
                'details': product_details,
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
        prefer_new = (state.suggestions.get('requested_more') or
                      state.suggestions.get('negative_feedback') or
                      (not state.suggestions.get('requirements_changed')
                       and not state.suggestions.get('decision_help')))
        unseen = [candidate for candidate in candidates if candidate.parent_asin not in state.shown_asins]
        if prefer_new and unseen:
            candidates = [replace(candidate, candidate_rank=index)
                          for index, candidate in enumerate(unseen, 1)]
        without_price = replace(state, hard_constraints={
            key: value for key, value in state.hard_constraints.items()
            if key not in {'price_min', 'price_max'}})
        price_blocked = any(
            product_id not in state.rejected_asins and row.get('found')
            and StateAwareRetriever.satisfies(self, product_id, without_price)
            for product_id, row in by_id.items())
        return RetrievalResultV2(
            candidate_set_id=f'{state.session_id}:{state.turn}:v{state.state_version}:a{request.attempt}',
            session_id=state.session_id, turn=state.turn, state_version=state.state_version,
            candidate_limit=request.candidate_limit, candidates=tuple(candidates),
            stats=RetrievalStats(), state_snapshot=state.to_dict(),
            legacy_requirements=requirements,
            warnings=(('search_tool scores retained; catalog-supported soft color, fit, fabric, or breathability preferences may reorder results; '
                       'price, inventory and variants are unknown.')
                      if ordered_color or any(state.soft_preferences.get(name) for name in
                                              ('style', 'material', 'fit_avoid', 'size', 'feature')) else
                      'search_tool ranking retained; price, inventory and variants are unknown.',
                      *(("budget_unverifiable: search_tool returned products without prices.",)
                        if price_blocked and any(key in state.hard_constraints
                                                 for key in ('price_min', 'price_max'))
                        else ())))

    def rerank(self, retrieval, *, top_k):
        soft = retrieval.state_snapshot.get('soft_preferences', {})
        color_choices = soft.get('color', ())
        ordered_color = (str(color_choices[0]['value']) if len(color_choices) == 2 and
                         float(color_choices[0]['weight']) > float(color_choices[1]['weight'])
                         else None)
        styles = [(str(row['value']), float(row['weight'])) for row in soft.get('style', ())
                  if row.get('value')]
        materials = [(str(row['value']), float(row['weight'])) for row in soft.get('material', ())
                     if row.get('value')]
        breathability_weight = max((float(row['weight']) for row in soft.get('feature', ())
                                    if str(row.get('value', '')).strip().casefold() == 'breathable'),
                                   default=0.0)
        avoid_fits = [str(row.get('value')) for row in
                      soft.get('fit_avoid', ())
                      if row.get('value')]
        sizes = [str(row.get('value')) for row in
                 soft.get('size', ())
                 if row.get('value')]
        candidates = list(retrieval.candidates)
        preferred_color = {candidate.parent_asin: bool(
            ordered_color and color_matches(candidate.product, ordered_color))
            for candidate in candidates}
        matched = {candidate.parent_asin: any(style_matches(candidate.product, style) for style, _ in styles)
                   for candidate in candidates}
        matched_material = {candidate.parent_asin: any(
            material_matches(candidate.product, material) for material, _ in materials)
            for candidate in candidates}
        breathable = {candidate.parent_asin: bool(breathability_weight and breathable_matches(candidate.product))
                      for candidate in candidates}
        weighted_match = {}
        for candidate in candidates:
            product = candidate.product
            weighted_match[candidate.parent_asin] = (
                max((float(row['weight']) for row in color_choices
                     if ordered_color and color_matches(product, str(row['value']))), default=0.0)
                + max((weight for style, weight in styles if style_matches(product, style)),
                      default=0.0)
                + max((weight for material, weight in materials
                       if material_matches(product, material)), default=0.0)
                + (breathability_weight if breathable[candidate.parent_asin] else 0.0)
            )
        disfavored = {candidate.parent_asin: any(style_matches(candidate.product, value)
                                                 for value in avoid_fits)
                      for candidate in candidates}
        sized = {candidate.parent_asin: any(size_matches(candidate.product, value)
                                                for value in sizes)
                 for candidate in candidates}
        preference_reranked = bool(styles and any(matched.values()) and not all(matched.values()))
        material_reranked = bool(materials and any(matched_material.values())
                                 and not all(matched_material.values()))
        color_reranked = bool(ordered_color and any(preferred_color.values())
                              and not all(preferred_color.values()))
        size_reranked = bool(sizes and any(sized.values()) and not all(sized.values()))
        breathable_reranked = any(breathable.values()) and not all(breathable.values())
        avoid_reranked = bool(avoid_fits and any(disfavored.values())
                              and not all(disfavored.values()))
        weighted_reranked = len({round(value, 8) for value in weighted_match.values()}) > 1
        if weighted_reranked or size_reranked or avoid_reranked:
            # Use only catalog-supported preference evidence. Equal evidence
            # retains the search tool's original order and scores.
            candidates.sort(key=lambda candidate: (
                -weighted_match[candidate.parent_asin] if weighted_reranked else 0.0,
                not sized[candidate.parent_asin] if size_reranked else False,
                disfavored[candidate.parent_asin] if avoid_reranked else False))
        rows = tuple(RankedCandidate(candidate.parent_asin, index,
            self.scores[candidate.parent_asin],
            (('search_tool original score; original order within equal-evidence group',)
             if weighted_reranked or size_reranked or avoid_reranked else ('search_tool original score and order',)) +
            (('catalog-supported primary color preference',) if preferred_color[candidate.parent_asin] else ()) +
            (('catalog-supported soft style match',) if matched[candidate.parent_asin] else ()) +
            (('catalog-supported soft material match',) if matched_material[candidate.parent_asin] else ()) +
            (('catalog-supported soft breathability match',) if breathable[candidate.parent_asin] else ()) +
            (('catalog-supported soft size match',) if sized[candidate.parent_asin] else ()) +
            (('explicit fit label conflicts with soft avoidance',)
             if disfavored[candidate.parent_asin] else ()))
            for index, candidate in enumerate(candidates[:top_k], 1))
        result = RankingResultV2(retrieval.candidate_set_id, retrieval.session_id,
            retrieval.turn, retrieval.state_version, rows,
            ('search_tool+supported_soft_preferences' if weighted_reranked and breathable_reranked else
             'search_tool+weighted_catalog_preferences' if weighted_reranked and material_reranked and
             (color_reranked or preference_reranked) else
             'search_tool+supported_soft_material_first' if weighted_reranked and material_reranked else
             'search_tool+supported_primary_color_first' if weighted_reranked and color_reranked else
             'search_tool+supported_soft_preferences' if size_reranked and
             (preference_reranked or avoid_reranked) else
             'search_tool+supported_soft_size_first' if size_reranked else
             'search_tool+supported_soft_fit_guidance' if avoid_reranked else
             'search_tool+supported_soft_style_first' if weighted_reranked and preference_reranked else 'search_tool'),
            score_semantics='uncalibrated')
        result.validate_against(retrieval, top_k=top_k)
        return result
