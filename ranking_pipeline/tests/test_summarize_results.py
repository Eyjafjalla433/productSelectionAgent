from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ranking_pipeline.summarize_results import render_markdown, row_from_result


class SummarizeResultsTests(unittest.TestCase):
    def test_row_from_result_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local-fusion.json"
            path.write_text(
                json.dumps(
                    {
                        "sample_count": 200,
                        "hit_rate_at_10": 0.975,
                        "mrr": 0.878429,
                        "mttc": 3.32,
                        "efficiency": 0.768,
                        "recommended_technical_score": 0.904629,
                        "scenario_metrics": {
                            "intent_override": {"hit_rate_at_10": 0.866667}
                        },
                    }
                ),
                encoding="utf-8",
            )

            row = row_from_result(path)
            markdown = render_markdown([row])

        self.assertEqual(row.file, "local-fusion.json")
        self.assertEqual(row.hit_rate_at_10, 0.975)
        self.assertIn("0.975000", markdown)
        self.assertIn("override_hit=0.866667", markdown)


if __name__ == "__main__":
    unittest.main()
