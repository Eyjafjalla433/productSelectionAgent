from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ranking_pipeline.distribution_alignment import (
    build_aligned_training_examples,
    summarize_examples,
    summarize_synthetic_rankings,
)
from ranking_pipeline.training_data import (
    build_public_training_examples,
    build_synthetic_training_examples,
)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def make_catalog(path: Path, asins: list[str]) -> None:
    rows = []
    for index, asin in enumerate(asins):
        rows.append(
            {
                "parent_asin": asin,
                "title": f"Cotton blue shirt {index}",
                "categories": ["Clothing", "Shirts"],
                "features": ["cotton", "blue"],
                "details": {"Color": "Blue"},
                "description": [],
                "store": "Store",
                "price": 10.0 + index,
            }
        )
    write_jsonl(path, rows)


class DistributionAlignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.root = root
        self.catalog = root / "catalog.jsonl"
        make_catalog(self.catalog, ["P00", "P01", "P02", "P10", "P11", "P12"])

        self.synthetic = root / "synthetic.jsonl"
        write_jsonl(
            self.synthetic,
            [
                {
                    "sample_id": "synthetic_0001",
                    "ground_truth": {"parent_asin": "P00"},
                },
                {
                    "sample_id": "synthetic_0002",
                    "ground_truth": {"parent_asin": "P01"},
                },
            ],
        )
        self.tiers = root / "tiers.jsonl"
        write_jsonl(
            self.tiers,
            [
                {"sample_id": "synthetic_0001", "quality_tier": "high_confidence"},
                {"sample_id": "synthetic_0002", "quality_tier": "low_likelihood"},
            ],
        )
        self.products = root / "products.csv"
        with self.products.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["parent_asin", "family", "leaf_category", "quality_tier", "selection_frequency"],
            )
            writer.writeheader()
            writer.writerows(
                [
                    {"parent_asin": "P00", "family": "shirts", "leaf_category": "Shirts", "quality_tier": "high_confidence", "selection_frequency": "1.0"},
                    {"parent_asin": "P01", "family": "shirts", "leaf_category": "Shirts", "quality_tier": "low_likelihood", "selection_frequency": "0.0"},
                    {"parent_asin": "P02", "family": "shirts", "leaf_category": "Shirts", "quality_tier": "probable", "selection_frequency": "0.6"},
                ]
            )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_synthetic_builder_filters_tier_and_samples_same_category(self) -> None:
        examples = build_synthetic_training_examples(
            self.synthetic,
            self.catalog,
            product_csv_path=self.products,
            tiers_path=self.tiers,
            negatives_per_positive=2,
            tier_filter=("high_confidence", "probable"),
            seed=0,
        )

        self.assertEqual(len(examples), 3)
        self.assertEqual(sum(example.label == 1.0 for example in examples), 1)
        self.assertEqual(sum(example.label == 0.0 for example in examples), 2)
        self.assertTrue(all(example.source == "synthetic" for example in examples))
        self.assertTrue(all(example.tier == "high_confidence" for example in examples))

    def test_synthetic_builder_excludes_public_targets(self) -> None:
        examples = build_synthetic_training_examples(
            self.synthetic,
            self.catalog,
            product_csv_path=self.products,
            tiers_path=self.tiers,
            negatives_per_positive=1,
            tier_filter=("high_confidence", "probable"),
            exclude_target_ids=("P00",),
            seed=0,
        )
        self.assertEqual(examples, [])

    def test_aligned_builder_combines_public_and_synthetic(self) -> None:
        public_set = self.root / "public.jsonl"
        write_jsonl(
            public_set,
            [{"sample_id": "public_0001", "ground_truth": {"parent_asin": "P10"}}],
        )
        public_top50 = self.root / "public_top50.jsonl"
        write_jsonl(
            public_top50,
            [{"sample_id": "public_0001", "parent_asins": ["P10", "P11", "P12"]}],
        )

        examples = build_aligned_training_examples(
            public_set,
            public_top50,
            self.synthetic,
            self.catalog,
            synthetic_product_csv_path=self.products,
            synthetic_tiers_path=self.tiers,
            public_negatives_per_positive=2,
            synthetic_negatives_per_positive=2,
            seed=0,
        )

        summary = summarize_examples(examples)
        self.assertEqual(summary.total_examples, 6)
        self.assertEqual(summary.public_examples, 3)
        self.assertEqual(summary.synthetic_examples, 3)
        self.assertIn("high_confidence", summary.tier_counts)

    def test_public_builder_falls_back_without_top50(self) -> None:
        public_set = self.root / "public.jsonl"
        write_jsonl(
            public_set,
            [{"sample_id": "public_0001", "ground_truth": {"parent_asin": "P10"}}],
        )

        examples = build_public_training_examples(
            public_set,
            None,
            self.catalog,
            negatives_per_positive=2,
            negative_pool_csv_path=self.products,
            seed=0,
        )

        self.assertEqual(len(examples), 3)
        self.assertEqual(sum(example.label == 1.0 for example in examples), 1)

    def test_synthetic_ranking_diagnostics_are_tiered(self) -> None:
        records = [
            {
                "sample_id": "synthetic_0001",
                "ground_truth": {"parent_asin": "P00"},
                "ranked_ids": ["P00", "P01", "P02"],
                "scores": [0.9, 0.7, 0.6],
                "over_general": False,
            },
            {
                "sample_id": "synthetic_0002",
                "ground_truth": {"parent_asin": "P01"},
                "ranked_ids": ["P02", "P01", "P00"],
                "scores": [0.4, 0.39, 0.38],
                "over_general": True,
            },
        ]
        tiers = {
            "synthetic_0001": "high_confidence",
            "synthetic_0002": "low_likelihood",
        }

        diagnostics = summarize_synthetic_rankings(records, tiers, top_k=10)

        self.assertEqual(diagnostics["over_general_rate"], 0.5)
        self.assertEqual(diagnostics["tier_metrics"]["high_confidence"]["recall"], 1.0)
        self.assertEqual(diagnostics["tier_metrics"]["low_likelihood"]["recall"], 1.0)
        self.assertEqual(diagnostics["score_distribution"]["count"], 6)


if __name__ == "__main__":
    unittest.main()
