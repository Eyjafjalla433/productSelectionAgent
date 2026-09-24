from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from techjam_agent.contracts import Requirements
from techjam_agent.ranking import LockedWeightedRrfTop10Reranker
from techjam_agent.retrieval import LiteTop50CandidateGenerator
from techjam_agent.retrieval import _append_unseen


class LockedPipelineTests(unittest.TestCase):
    def test_append_unseen_never_expands_a_full_candidate_pool(self) -> None:
        selected = [f"P{index:02d}" for index in range(50)]

        _append_unseen(selected, [f"X{index:02d}" for index in range(20)], 50)

        self.assertEqual(selected, [f"P{index:02d}" for index in range(50)])

    def test_generator_returns_50_and_reranker_returns_10_from_same_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.jsonl"
            with catalog_path.open("w", encoding="utf-8") as handle:
                for index in range(60):
                    product = {
                        "parent_asin": f"P{index:02d}",
                        "title": f"Cotton blue shirt {index}",
                        "categories": ["Clothing", "Women's Shirts"],
                        "features": ["cotton", "machine washable", "lightweight"],
                        "details": {"Color": "Blue"},
                        "description": [],
                        "store": "Example",
                        "rating_number": 60 - index,
                    }
                    handle.write(json.dumps(product) + "\n")

            generator = LiteTop50CandidateGenerator(catalog_path)
            candidate_set = generator.generate(
                Requirements(
                    category="Women's Shirts",
                    hard_constraints=("cotton", "color: blue"),
                    soft_preferences=("machine washable", "lightweight"),
                ),
                session_id="session",
                turn=3,
            )
            result = LockedWeightedRrfTop10Reranker().rerank(candidate_set, top_k=10)

        self.assertEqual(len(candidate_set.candidates), 50)
        self.assertEqual(len(result.ranked_candidates), 10)
        result.validate_against(candidate_set, top_k=10)
        self.assertTrue(all(candidate.source_ranks for candidate in candidate_set.candidates))


if __name__ == "__main__":
    unittest.main()
