"""Summarize saved local-evaluator result JSONs into a comparison table."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RESULTS_ROOT = Path(__file__).resolve().parent / "results"
DEFAULT_OUTPUT = RESULTS_ROOT / "README.md"


@dataclass(frozen=True)
class ResultRow:
    file: str
    sample_count: int
    hit_rate_at_10: float
    mrr: float
    mttc: float | None
    efficiency: float
    technical_score: float
    mode_note: str


def load_result(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def row_from_result(path: Path) -> ResultRow:
    data = load_result(path)
    scenario = data.get("scenario_metrics") or {}
    override = scenario.get("intent_override") or {}
    note_parts = []
    if override:
        note_parts.append(f"override_hit={override.get('hit_rate_at_10')}")
    return ResultRow(
        file=path.name,
        sample_count=int(data.get("sample_count") or 0),
        hit_rate_at_10=float(data.get("hit_rate_at_10") or 0.0),
        mrr=float(data.get("mrr") or 0.0),
        mttc=(
            None if data.get("mttc") is None else float(data["mttc"])
        ),
        efficiency=float(data.get("efficiency") or 0.0),
        technical_score=float(data.get("recommended_technical_score") or 0.0),
        mode_note=", ".join(note_parts),
    )


def format_number(value: float | None, digits: int = 6) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def render_markdown(
    rows: list[ResultRow],
    synthetic_diagnostics: dict[str, Any] | None = None,
) -> str:
    synthetic_lines: list[str] = []
    if synthetic_diagnostics:
        record_count = int(synthetic_diagnostics.get("record_count") or 0)
        top_k = int(synthetic_diagnostics.get("top_k") or 0)
        over_general_rate = synthetic_diagnostics.get("over_general_rate")
        score_distribution = synthetic_diagnostics.get("score_distribution") or {}
        tier_metrics = synthetic_diagnostics.get("tier_metrics") or {}

        synthetic_lines = [
            f"- Records: `{record_count}`, Top-K: `{top_k}`, "
            f"over-general rate: `{format_number(over_general_rate)}`.",
            "- Per-tier recall:",
        ]
        for tier, metrics in tier_metrics.items():
            count = int(metrics.get("count") or 0)
            hits = int(metrics.get("hits") or 0)
            recall = metrics.get("recall")
            synthetic_lines.append(
                f"  - `{tier}`: {hits}/{count} "
                f"(`{format_number(recall)}`)."
            )
        score_count = int(score_distribution.get("count") or 0)
        score_mean = score_distribution.get("mean")
        score_median = score_distribution.get("median")
        synthetic_lines.append(
            "- Fallback-score distribution: "
            f"{score_count} scores, mean `{format_number(score_mean)}`, "
            f"median `{format_number(score_median)}`."
        )

    lines = [
        "# Ranking Pipeline Results",
        "",
        "Saved local-evaluator results under `ranking_pipeline/results/`.",
        "Public-200 official local-evaluator diagnostics. The `exact` rows use",
        "the local BGE dense embeddings and catalog assets.",
        "",
        "## Mode Definitions",
        "",
        "- `locked`: retrieval candidate generation plus the repository's",
        "  `LockedWeightedRrfTop10Reranker`; no local model and no ranking-pipeline",
        "  LLM path.",
        "- `hybrid`: ranking-pipeline hard-constraint filter + locked weighted-RRF",
        "  pre-rank, but no local model scoring.",
        "- `local`: ranking-pipeline `HybridContextualReranker` with the Qwen3",
        "  reranker pointwise scores fused with the locked pre-rank.",
        "- `policy`: `local` plus `RecommendationClarificationPolicy` deciding",
        "  between recommend and clarify.",
        "",
        "`retrieval-mode=exact` means `ExactDenseTop50CandidateGenerator`, the",
        "official dense path with BGE small embeddings. `retrieval-mode=lite` is",
        "the local non-dense smoke path.",
        "",
        "## Metrics",
        "",
        "- Hit@10: target appears in the first 10 recommendations.",
        "- MRR: mean `1 / best_rank`; zero when the target is never returned.",
        "- MTTC: mean first-hit turn; a miss counts as `11`, so lower is better.",
        "- Efficiency: `clamp((11 - MTTC) / 10, 0, 1)`.",
        "- Technical: `0.50 * Hit@10 + 0.30 * MRR + 0.20 * Efficiency`.",
        "",
        "## Key Findings",
        "",
        "- `locked-exact` is currently the strongest submission configuration:",
        "  Hit@10 `0.970`, MRR `0.870548`, MTTC `3.325`, Technical `0.899664`.",
        "- `local-exact` preserves or improves Hit@10 (`0.975`) but lowers MRR",
        "  (`0.786365`) compared with locked.",
        "- Enabling the clarification policy sharply increases MTTC to `9.955`;",
        "  it is not recommended for this public evaluator.",
        "- A pointwise-weight sweep `0.1/0.35/0.6` did not recover the locked MRR.",
        "",
        "## Synthetic 3021 Diagnostics",
        "",
        "`synthetic-3021-diagnostics.json` reports per-tier recall, fallback score",
        "distribution, and over-general rate for the synthetic proxy set. It is",
        "diagnostic only and is not an official ranking score.",
    ]
    if synthetic_lines:
        lines.extend(synthetic_lines)
    lines.extend(
        [
            "",
            "## Result Table",
            "",
            "| file | n | Hit@10 | MRR | MTTC | Efficiency | Technical | notes |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row.file,
                    str(row.sample_count),
                    format_number(row.hit_rate_at_10),
                    format_number(row.mrr),
                    format_number(row.mttc),
                    format_number(row.efficiency),
                    format_number(row.technical_score),
                    row.mode_note or "-",
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-root",
        type=Path,
        default=RESULTS_ROOT,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
    )
    args = parser.parse_args()

    paths = sorted(args.results_root.glob("*.json"))
    if not paths:
        raise SystemExit(f"No result JSON files found in {args.results_root}")
    evaluator_paths = []
    for path in paths:
        try:
            data = load_result(path)
        except json.JSONDecodeError:
            continue
        if "hit_rate_at_10" in data and "sample_count" in data:
            evaluator_paths.append(path)
    if not evaluator_paths:
        raise SystemExit(f"No evaluator result JSON files found in {args.results_root}")
    rows = [row_from_result(path) for path in evaluator_paths]
    rows.sort(
        key=lambda row: (
            -row.technical_score,
            -row.hit_rate_at_10,
            row.file,
        )
    )
    diagnostics_path = args.results_root / "synthetic-3021-diagnostics.json"
    synthetic_diagnostics = None
    if diagnostics_path.exists():
        try:
            synthetic_diagnostics = load_result(diagnostics_path)
        except json.JSONDecodeError:
            synthetic_diagnostics = None
    markdown = render_markdown(rows, synthetic_diagnostics=synthetic_diagnostics)
    args.output.write_text(markdown, encoding="utf-8")
    print(markdown)


if __name__ == "__main__":
    main()
