# Shopping Copilot AI Conversational Search and Recommendations

This branch, `codex/system-integration`, collects the existing modules for
component 5 (evaluation, orchestration, and integration). It starts from
`ranking_pipeline` at `f91b3d5`; the matching modules from all five source
branches have been verified by Git tree hashes. This is a source snapshot,
not an automatic synchronizer or a claim that the complete pipeline is ready.

Updated on 2026-09-01: merged `ranking_pipeline` through `ab77248` and applied
the `main` intent-contract update `4fae36a`. See the
[sync and verification record](docs/integration/SYNC_2026-09-01.md) for current
source versions, local data compatibility setup, and test scope.

The [version 2 interface changes](docs/integration/INTERFACES_V2_2026-09-01.md)
define explicit intent updates, detached state exports, and variable-size
retrieval/ranking results. The new `shopping_agent.FinalAgent` now consumes these
contracts end to end. Legacy entry points remain available for comparison.

**Integrated CPU entry:** see [完整链条与断点调试](docs/integration/INTEGRATED_PIPELINE_DEBUG.md).
It runs `User -> 1 -> 3 -> 4A -> 2 -> 4B -> Response -> State feedback`, uses
the actual catalog and module 4's CPU hybrid reranker, and supports variable-size
candidate pools, explicit slot changes, bounded clarification/retry, and a
ten-turn limit. Dense and Qwen inference are not validated on this machine.

See the [Chinese delivery audit and integration checklist](docs/integration/DELIVERY_AUDIT_2026-08-31.md)
for module readiness, actual test results, known gaps, and source versions.

The [official requirements and data installation report](docs/integration/OFFICIAL_REQUIREMENTS_AND_DATA_2026-08-31.md)
records the verified participant-kit download, local data paths, and fresh
public-200 BM25 and locked+lite evaluation. This supersedes the earlier audit's
statement that catalog/runtime data were absent; exact and Qwen inference are
still unverified on this machine.

Project files are organized as independent modules:

```text
intent-recognition/          current-message intent and query understanding
conversation-state-memory/   multi-turn state and context programming
retrieval-and-reranking/     complete-requirements Top50 and Top10 ranking
ranking_pipeline/            contextual ranking, models, and clarification
synthetic-data-3021/          synthetic proxy data and provenance
shopping_agent/              versioned orchestration and module adapters
docs/integration/            component 5 audit and integration evidence
```

## Integration boundary

`intent-recognition/` parses one message at a time. A separate state or dialogue
component must accumulate the disclosed category, hard constraints, and soft
preferences. Only after those requirements are considered complete should they
be passed to `retrieval-and-reranking/`.

The standalone retrieval diagnostics measure a **complete-information
condition**. They do not prove that two clarification questions will recover all
requirements in the organizer's private sessions. The ranking module also has
saved interactive public-200 results; those are a separate evaluation condition.
The legacy `RankingAgent` optional flags do not make Router/State authoritative.
Use `shopping_agent.FinalAgent` for the new full-state-controlled workflow;
its acceptance results are separate from the legacy baseline scores.

## Collaboration

Please create a feature branch, run the tests for the module you changed, and
open a pull request instead of pushing directly to `main`. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the project workflow and large-file
rules.
