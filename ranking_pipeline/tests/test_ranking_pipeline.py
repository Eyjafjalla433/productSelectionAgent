from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from techjam_agent.contracts import Candidate, CandidateSet, Requirements
from ranking_pipeline.contextual_ranking import (
    HybridContextualReranker,
    assess_candidate_pool,
)
from ranking_pipeline.prompt import LLMRankResult
from ranking_pipeline.qwen_reranker import Qwen3Reranker, format_pair, product_text
from ranking_pipeline.agent import RankingAgent
from ranking_pipeline.training_data import build_public_training_examples


def make_candidate(index: int) -> Candidate:
    return Candidate(
        parent_asin=f"P{index:02d}",
        candidate_rank=index + 1,
        source_ranks={"route": index + 1},
        product={
            "title": f"Product {index}",
            "categories": ["Clothing"],
            "features": ["cotton", "blue"],
            "details": {},
            "description": [],
            "store": "Test Store",
        },
    )


def make_candidate_set(count: int = 50) -> CandidateSet:
    return CandidateSet(
        candidate_set_id="session:3",
        session_id="session",
        turn=3,
        requirements=Requirements("Clothing", ("cotton", "blue"), ()),
        candidates=tuple(make_candidate(index) for index in range(count)),
    )


class QwenAdapterTests(unittest.TestCase):
    def test_format_pair_contains_structured_delimiters(self) -> None:
        text = format_pair("cotton; blue", "A blue cotton shirt")
        self.assertIn("<Instruct>", text)
        self.assertIn("<Query>", text)
        self.assertIn("<Document>", text)
        self.assertIn("A blue cotton shirt", text)

    def test_rank_candidates_uses_pointwise_scores(self) -> None:
        candidates = [make_candidate(index) for index in range(3)]
        ranker = Qwen3Reranker()
        with patch.object(ranker, "score_pairs", return_value=[0.2, 0.9, 0.4]):
            result = ranker.rank_candidates(
                Requirements("Clothing", ("cotton", "blue"), ()),
                candidates,
                top_k=2,
            )
        self.assertEqual(result.ranked_ids, ("P01", "P02"))
        self.assertGreater(result.confidence, 0.8)
        self.assertFalse(result.need_clarification)
        self.assertEqual(result.scores, (0.9, 0.4))
        self.assertTrue(all(result.reasons))

    def test_hybrid_uses_qwen_pointwise_score_fusion(self) -> None:
        class FakeQwen(Qwen3Reranker):
            def score_candidates(self, *args, **kwargs):
                del args, kwargs
                return {"P00": 0.2, "P01": 0.9, "P02": 0.4}

        reranker = HybridContextualReranker(llm_ranker=FakeQwen(), top_n=20)
        result = reranker.rerank(make_candidate_set(), top_k=2)
        self.assertEqual(
            [candidate.parent_asin for candidate in result.ranked_candidates],
            ["P01", "P00"],
        )
        self.assertIsNotNone(reranker.last_model_result)

    def test_assess_candidate_pool_flags_low_separation(self) -> None:
        ranked = [
            type("R", (), {"score": 0.8})(),
            type("R", (), {"score": 0.79})(),
        ]
        metrics = assess_candidate_pool(
            ranked,
            hard_hit_rate=0.5,
            candidate_count=20,
            strategy="local",
        )
        self.assertTrue(metrics.is_over_general)


class TrainingDataTests(unittest.TestCase):
    def test_build_public_training_examples_keeps_candidate_snapshots_public(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalog = {
                "P00": {
                    "parent_asin": "P00",
                    "title": "Target cotton blue shirt",
                    "categories": ["Clothing"],
                    "features": ["cotton", "blue"],
                    "details": {"Color": "Blue"},
                    "description": [],
                    "store": "Store",
                    "price": 12.0,
                    "average_rating": 4.0,
                    "rating_number": 10,
                },
                "P01": {
                    "parent_asin": "P01",
                    "title": "Polyester red dress",
                    "categories": ["Clothing"],
                    "features": ["polyester", "red"],
                    "details": {},
                    "description": [],
                    "store": "Store",
                },
                "P02": {
                    "parent_asin": "P02",
                    "title": "Wool coat",
                    "categories": ["Clothing"],
                    "features": ["wool"],
                    "details": {},
                    "description": [],
                    "store": "Store",
                },
            }
            public_set = [
                {
                    "sample_id": "public_0001",
                    "ground_truth": {"parent_asin": "P00"},
                }
            ]
            top50 = [
                {"sample_id": "public_0001", "parent_asins": ["P00", "P01", "P02"]}
            ]
            catalog_path = root / "catalog.jsonl"
            public_path = root / "public_set.jsonl"
            top50_path = root / "top50.jsonl"
            catalog_path.write_text(
                "\n".join(json.dumps(row) for row in catalog.values()) + "\n",
                encoding="utf-8",
            )
            public_path.write_text(
                "\n".join(json.dumps(row) for row in public_set) + "\n",
                encoding="utf-8",
            )
            top50_path.write_text(
                "\n".join(json.dumps(row) for row in top50) + "\n",
                encoding="utf-8",
            )

            examples = build_public_training_examples(
                public_path,
                top50_path,
                catalog_path,
                negatives_per_positive=2,
                seed=0,
            )

        self.assertEqual(len(examples), 3)
        self.assertEqual(sum(example.label == 1.0 for example in examples), 1)
        self.assertEqual(sum(example.label == 0.0 for example in examples), 2)
        self.assertIn("cotton", examples[0].document)


class RankingAgentConfigurationTests(unittest.TestCase):
    @patch("techjam_agent.retrieval.LiteTop50CandidateGenerator")
    def test_question_limit_is_explicit_and_validated(self, generator) -> None:
        generator.return_value = object()
        agent = RankingAgent("unused.jsonl", clarification_turn_limit=1)
        self.assertEqual(agent._clarification_turn_limit, 1)
        with self.assertRaises(ValueError):
            RankingAgent("unused.jsonl", clarification_turn_limit=10)


if __name__ == "__main__":
    unittest.main()
