"""Intent routing and query understanding for the TechJam shopping agent."""

from .catalog_lexicon import load_catalog_brands, load_catalog_categories
from .models import IntentResult, SlotUpdate
from .router import IntentRouter

__all__ = ["IntentResult", "SlotUpdate", "IntentRouter", "load_catalog_brands", "load_catalog_categories"]
