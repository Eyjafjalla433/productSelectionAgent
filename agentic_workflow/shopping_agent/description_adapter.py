"""Connect the sibling description module without writing into its package."""
from copy import deepcopy
import importlib
import sys


class DescriptionComparisonAdapter:
    def __init__(self, provider=None):
        self.provider = provider

    @property
    def cache_identity(self):
        return ('description-comparison.v1', getattr(self.provider, 'name', None),
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
        return module.DescriptionComparison(self.provider).compare(deepcopy(handoff))
