# Description module integration

The workflow imports the sibling `description_module` through
`shopping_agent/description_adapter.py`. All integration code lives in
`agentic_workflow`; neither `description_module` nor `search_tool` is edited.
Keep both sibling packages alongside this folder when running the application.

## Run

```sh
python -B -m agentic_workflow --demo
```

Open http://127.0.0.1:8000 and try:

1. `I need a blue cotton dress under $50.`
2. `Compare #1 and #2.`
3. `I prefer pockets.`
4. `Compare #1 and #2.`
5. `Finalize my selection.`

Omit `--demo` to use the actual search index. Its current product records do
not include prices or full descriptions, so start with `I need a blue dress`
instead of requiring a verified price cap.

The default mode uses the module's `offline_preview`: direct catalog facts,
explicit unknowns, and no model-generated personalized advice. For the full
three-stage pipeline, use the existing configured model provider:

```sh
python -B -m agentic_workflow --demo --model-provider local --model YOUR_MODEL --model-base-url http://127.0.0.1:11434/v1
```

`--model-provider deepseek` uses the existing `DEEPSEEK_API_KEY` environment
variable. The same provider handles optional requirement assistance and the
description pipeline. Provider settings and cloud disclosure stay consistent.
This integration adds no credentials or automatic external model calls.

## Data flow

Search and ranking → displayed results → user selects up to three products →
selection handoff → `DescriptionComparison.compare()` → comparison panel and
JSON export.

The handoff carries bounded titles, descriptions, bullets, details, prices and
ratings together with current hard requirements, soft preferences and exclusions.
The search adapter preserves description fields when supplied; it does not
invent full descriptions or fetch them from another source.

The module owns attribute extraction, objective comparison, personalized advice,
and source-quote checks. Its complete `description-comparison.v1` result appears
under `handoff.comparison_assist` in chat, `/api/handoff`, audit receipts and exports.
Comparison preserves the selection order and does not rerank search results.

The page separates listed facts from personalized advice, labels inferences,
shows missing cells as Unknown, and groups wholly unavailable dimensions in an
expandable section. Quotes are available through “View source.” The terminal
demo and generated standalone showcase also include the module's output.

Repeated comparison, finalization and export reuse a session cache. Changed
requirements, selected products, supplied catalog facts, or model identity
invalidate it. Cached calls report zero additional tokens. Failed model stages
retain available results with `partial` status; the shopper can still finalize.

## Verification

```sh
python -B -m unittest agentic_workflow.tests.test_description_integration -v
python -B -m agentic_workflow.verify
python -B -m agentic_workflow.mvp.ui_smoke
python -B -m agentic_workflow.showcase --backend demo
```

Integration tests run the actual shared module with simulated model responses,
covering source passthrough, selection order, evidence rejection, three-stage
usage, cache reuse and invalidation, and partial failures. Browser checks cover
the table, inference and unknown labels, personalized advice, safe text rendering,
and clearing stale comparisons. The isolated verifier copies the description
dependency into a temporary test directory without altering the original.

Real model quality still requires evaluation with a configured provider; quote
occurrence checks alone do not prove the model's interpretation is correct.

Verified after integration: all 505 workflow regressions, the shared module's
eight existing tests, and 38 browser checks passed. An actual-search replay
returned ten blue dresses, compared three, finalized them, reused the cached
description result on export, and passed the conversation audit. SHA-256 checks
confirmed that all 24 files across `description_module` and `search_tool` stayed
unchanged. Three-stage model behavior was tested with simulated responses;
the actual-search replay used the module's offline preview.
