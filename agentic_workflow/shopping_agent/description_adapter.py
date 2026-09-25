"""Connect the sibling description module without writing into its package."""
from copy import deepcopy
from dataclasses import dataclass
import importlib
import math
import sys


DIRECT_FIELDS = {
    'material': 'material', 'fabric': 'material',
    'fabric type': 'material', 'fabric composition': 'fabric_composition',
    'size': 'size', 'fit': 'fit', 'fit type': 'fit',
    'care': 'care', 'care instructions': 'care',
    'weight': 'weight', 'dimensions': 'dimensions', 'length': 'length',
    'brand': 'brand', 'color': 'color', 'model': 'model',
}
UNKNOWN = {'value': None, 'evidence': None, 'source_field': None, 'source_type': None}


@dataclass
class _Result:
    data: dict

    def to_dict(self):
        return deepcopy(self.data)


def _direct_facts(data, handoff):
    """Copy unambiguous labeled metadata; never derive composition or advice."""
    products = {product['parent_asin']: product for product in handoff['selected_products']}
    additions = []
    for profile in data['product_profiles']:
        details = products[profile['parent_asin']].get('details') or {}
        if not isinstance(details, dict):
            continue
        candidates = {}
        for key, value in details.items():
            dimension = DIRECT_FIELDS.get(' '.join(str(key).split()).casefold())
            if not dimension or isinstance(value, bool) or not isinstance(value, (str, int, float)):
                continue
            if isinstance(value, float) and not math.isfinite(value):
                continue
            quote = str(value)
            normalized = ' '.join(quote.split()).casefold()
            if normalized in {'', '-', 'n/a', 'na', 'none', 'null', 'unknown', 'not specified', 'not provided'}:
                continue
            candidates.setdefault(dimension, []).append((normalized, str(key), quote))
        for dimension, values in candidates.items():
            if (profile['attributes'].get(dimension, {}).get('value') is not None or
                    len({value[0] for value in values}) != 1):
                continue
            _, key, quote = values[0]
            attribute = {'value': quote, 'evidence': quote,
                         'source_field': 'details.' + key, 'source_type': 'explicit'}
            profile['attributes'][dimension] = attribute
            additions.append({'parent_asin': profile['parent_asin'], 'dimension': dimension, **attribute})
    dimensions = list(dict.fromkeys(key for profile in data['product_profiles'] for key in profile['attributes']))
    for profile in data['product_profiles']:
        for dimension in dimensions:
            profile['attributes'].setdefault(dimension, deepcopy(UNKNOWN))
    objective = data['objective_comparison']
    objective['comparison_matrix'] = [
        {'dimension': dimension, 'values': {profile['parent_asin']: deepcopy(profile['attributes'][dimension])
                                          for profile in data['product_profiles']}}
        for dimension in dimensions]
    objective['unknowns'] = [
        {'parent_asin': profile['parent_asin'],
         'attributes': [key for key, value in profile['attributes'].items() if value['value'] is None]}
        for profile in data['product_profiles']]
    data['provenance']['workflow_direct_fields'] = additions
    return data


class DescriptionComparisonAdapter:
    def __init__(self, provider=None):
        self.provider = provider

    @property
    def cache_identity(self):
        return ('description-comparison.v1', 'direct-fields.v1', getattr(self.provider, 'name', None),
                getattr(self.provider, 'model', None))

    def compare(self, handoff):
        previous = sys.dont_write_bytecode
        try:
            sys.dont_write_bytecode = True
            module = importlib.import_module('description_module')
        finally:
            sys.dont_write_bytecode = previous
        # The shared module owns extraction, evidence checks and personalization.
        # It handles provider failures and supplies an offline preview by itself.
        result = module.DescriptionComparison(self.provider).compare(deepcopy(handoff))
        if self.provider is None:
            return _Result(_direct_facts(result.to_dict(), handoff))
        return result
