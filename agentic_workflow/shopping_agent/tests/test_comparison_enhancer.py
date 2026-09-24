import unittest

from shopping_agent.comparison_enhancer import ComparisonEnhancer
from shopping_agent.model_provider import ModelProviderError, StructuredModelResult


class FakeProvider:
    name = "fake"
    model = "fake-json"

    def __init__(self, data=None, failure=False):
        self.data = data or {}
        self.failure = failure

    def complete_json(self, **kwargs):
        if self.failure:
            raise ModelProviderError("offline")
        return StructuredModelResult(self.data, self.model, self.name, {"prompt_tokens": 20, "completion_tokens": 9}, 3.5)


def handoff():
    return {
        "requirements": {"hard": {"color": "blue"}},
        "selected_products": [
            {
                "parent_asin": "A1",
                "title": "Blue cotton shirt",
                "price": 24.5,
                "average_rating": 4.6,
                "categories": ["Shirts"],
                "product_description": ["A lightweight shirt for warm days."],
                "product_bullet_points": ["Machine washable cotton fabric"],
                "details": {"Care": "Machine wash"},
                "requirement_match": {"hard_coverage": 1.0},
            }
        ],
    }


class ComparisonEnhancerTests(unittest.TestCase):
    def test_accepts_only_known_products_with_exact_catalog_evidence(self):
        provider = FakeProvider({"products": [
            {"parent_asin": "A1", "pros": [
                {"text": "Easy care", "evidence": "Machine washable cotton fabric"},
                {"text": "Invented waterproofing", "evidence": "fully waterproof"},
            ], "cons": [{"text": "Warm-weather focus", "evidence": "lightweight shirt for warm days"}]},
            {"parent_asin": "UNKNOWN", "pros": [{"text": "Bad", "evidence": "anything"}], "cons": []},
        ]})
        outcome = ComparisonEnhancer(provider).compare(handoff())
        self.assertEqual(len(outcome.products), 1)
        self.assertEqual(len(outcome.products[0]["pros"]), 1)
        self.assertEqual(outcome.products[0]["pros"][0]["source"], "model_catalog_quote")
        self.assertEqual(len(outcome.products[0]["cons"]), 1)
        self.assertEqual(outcome.usage, {"prompt_tokens": 20, "completion_tokens": 9})

    def test_failure_is_zero_token_fallback(self):
        outcome = ComparisonEnhancer(FakeProvider(failure=True)).compare(handoff())
        self.assertEqual(outcome.products, ())
        self.assertEqual(outcome.usage, {"prompt_tokens": 0, "completion_tokens": 0})
        self.assertEqual(outcome.warning, "ModelProviderError")


if __name__ == "__main__":
    unittest.main()
