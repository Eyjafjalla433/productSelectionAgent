import inspect
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent import Agent
from submission_tools.prepare_data import install_archive
from submission_tools.common import sha256
from submission_tools.build import check_file


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = Path(self.temporary.name) / "catalog.jsonl"
        self.catalog.write_text(json.dumps({"parent_asin": "TEST_BLUE", "title": "blue cotton dress", "categories": ["Dresses"], "price": 30, "store": "Acme", "rating_number": 1}) + "\n")

    def tearDown(self):
        self.temporary.cleanup()

    def test_official_export_and_signature(self):
        self.assertEqual(list(inspect.signature(Agent.respond).parameters), ["self", "session_id", "user_message", "turn", "top_k"])
        self.assertEqual(list(inspect.signature(Agent.reset).parameters), ["self", "session_id", "user_profile"])

    def test_catalog_override_and_offline_output(self):
        with patch.dict("os.environ", {"SHOPPING_CATALOG_PATH": str(self.catalog)}), patch("socket.create_connection", side_effect=AssertionError("network not allowed")):
            agent = Agent(orchestration_mode="adaptive")
            agent.reset("s", {})
            response = agent.respond("s", "I need a blue dress under $50.", 1, 10)
        self.assertEqual(response["recommendations"][0]["parent_asin"], "TEST_BLUE")
        self.assertEqual(set(response), {"message", "ask_attribute", "recommendations", "usage"})
        self.assertEqual(response["usage"], {"prompt_tokens": 0, "completion_tokens": 0})

    def test_submitted_default_uses_fixed_warmup_and_dynamic_post_policy(self):
        agent = Agent(self.catalog)
        agent.reset("s", {})
        response = agent.respond("s", "I need a blue dress under $50.", 1, 10)
        self.assertEqual(agent.orchestration_mode, "score_compat")
        self.assertEqual(agent.clarification_mode, "fixed_two_dynamic")
        self.assertEqual(agent.pre_policy.minimum_questions, 2)
        self.assertEqual(agent.post_policy.max_questions, 3)
        self.assertEqual(response["ask_attribute"], "other")
        self.assertEqual(response["recommendations"], [])

    def test_catalog_is_not_modified(self):
        before = sha256(self.catalog)
        agent = Agent(self.catalog)
        agent.reset("s", {})
        agent.respond("s", "I need a dress.", 1, 10)
        self.assertEqual(sha256(self.catalog), before)

    def test_missing_catalog_fails_with_setup_instruction(self):
        with self.assertRaisesRegex(FileNotFoundError, "prepare_data"):
            Agent(Path(self.temporary.name) / "missing.jsonl")

    def test_wrong_kit_archive_rejected_before_install(self):
        archive = Path(self.temporary.name) / "wrong.zip"
        archive.write_bytes(b"not an official kit")
        destination = Path(self.temporary.name) / "output"
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            install_archive(archive, destination)
        self.assertFalse(destination.exists())

    def test_turn_11_rejected(self):
        agent = Agent(self.catalog)
        agent.reset("s", {})
        with self.assertRaises(ValueError):
            agent.respond("s", "again", 11, 10)


if __name__ == "__main__":
    unittest.main()
