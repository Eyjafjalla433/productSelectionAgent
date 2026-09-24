import json
import unittest

from intent_router.models import SlotUpdate
from shopping_agent.model_provider import (
    ModelProviderError,
    OpenAICompatibleJsonProvider,
    StructuredModelResult,
)
from shopping_agent.requirement_enhancer import RequirementEnhancer


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit):
        return self.payload[:limit]


class FakeProvider:
    name = "fake"
    model = "fake-json"

    def __init__(self, data=None, error=False):
        self.data = data or {"updates": []}
        self.error = error

    def complete_json(self, **kwargs):
        if self.error:
            raise ModelProviderError("offline")
        return StructuredModelResult(
            self.data,
            self.model,
            self.name,
            {"prompt_tokens": 12, "completion_tokens": 4},
            5.0,
        )


class ModelProviderTests(unittest.TestCase):
    def test_openai_compatible_json_envelope_and_usage(self):
        captured = {}

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization")
            captured["body"] = json.loads(request.data)
            captured["timeout"] = timeout
            return FakeResponse(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"updates": []})},
                        }
                    ],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 3},
                }
            )

        provider = OpenAICompatibleJsonProvider(
            name="deepseek",
            base_url="https://api.deepseek.com",
            model="deepseek-flash",
            api_key="test-secret",
            opener=opener,
        )
        result = provider.complete_json(system="Return JSON.", user="{}")
        self.assertEqual(captured["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(captured["authorization"], "Bearer test-secret")
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(result.usage, {"prompt_tokens": 9, "completion_tokens": 3})

    def test_remote_plain_http_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleJsonProvider(
                name="unsafe",
                base_url="http://example.com/v1",
                model="model",
                api_key=None,
            )

    def test_enhancer_accepts_only_grounded_non_conflicting_updates(self):
        provider = FakeProvider(
            {
                "updates": [
                    {"slot": "category", "operation": "set", "values": ["running shoes"], "constraint_type": "hard", "evidence": "I need running shoes"},
                    {"slot": "material", "operation": "set", "values": ["merino wool"], "constraint_type": "hard", "evidence": "merino wool"},
                    {"slot": "color", "operation": "set", "values": ["purple"], "constraint_type": "hard", "evidence": "running shoes"},
                    {"slot": "brand", "operation": "set", "values": ["Acme"], "constraint_type": "hard", "evidence": "I need running shoes"},
                    {"slot": "material", "operation": "exclude", "values": ["leather"], "constraint_type": None, "evidence": "not leather"},
                ]
            }
        )
        deterministic = (SlotUpdate("category", "set", ("shoes",), "hard", 0.9, "running shoes"),)
        outcome = RequirementEnhancer(provider).enhance(
            "I need running shoes in merino wool, not leather.", deterministic
        )
        self.assertEqual(
            [(update.slot, update.operation, update.constraint_type) for update in outcome.updates],
            [("material", "set", "soft")],
        )
        self.assertEqual(outcome.usage["prompt_tokens"], 12)

    def test_enhancer_failure_is_a_zero_token_fallback(self):
        outcome = RequirementEnhancer(FakeProvider(error=True)).enhance("teal shoes", ())
        self.assertEqual(outcome.updates, ())
        self.assertEqual(outcome.usage, {"prompt_tokens": 0, "completion_tokens": 0})
        self.assertEqual(outcome.warning, "ModelProviderError")

    def test_enhancer_unicode_grounding_accepts_chinese_evidence(self):
        provider = FakeProvider(
            {
                "updates": [
                    {"slot": "feature", "operation": "set", "values": ["防晒"], "constraint_type": "soft", "evidence": "希望有防晒功能"},
                    {"slot": "material", "operation": "exclude", "values": ["皮革"], "constraint_type": "hard", "evidence": "不要皮革"},
                    {"slot": "color", "operation": "set", "values": ["紫色"], "constraint_type": "hard", "evidence": "希望有防晒功能"},
                ]
            }
        )
        outcome = RequirementEnhancer(provider).enhance("希望有防晒功能，不要皮革。", ())
        self.assertEqual(
            [(update.slot, update.operation, update.values) for update in outcome.updates],
            [("feature", "set", ("防晒",)), ("material", "exclude", ("皮革",))],
        )


if __name__ == "__main__":
    unittest.main()
