# C — Agent and backend MVP completion audit

Audit scope: the user-provided C responsibility, not A's ranking metrics, B's
copy quality ownership, or D's production deployment and visual polish.

## Requirement-to-evidence matrix

| C requirement | Authoritative implementation/evidence | Result |
|---|---|---|
| Multi-turn conversation and isolated sessions | `AgentRuntime`, strict sequential turns, TTL/LRU cleanup, Agent-state cleanup; MVP runtime/HTTP tests | Proven |
| Requirement parsing and evolving state | `TurnIntentRouter` → `StructuredStateMemoryManager`; hard/soft/excluded evidence and diffs; 20 intent tests | Proven |
| Clarify/retrieve/tool orchestration | Observable `1_intent → 3_state → 4A → 2_retrieval → 4B` trace, bounded retry that never relaxes hard constraints | Proven |
| Bounded Top 10 from the real catalog | `FinalAgent.respond(..., top_k=10)` plus `python -m mvp.smoke`: 50,000 catalog rows, 10 shown, 10/10 hard-supported for the Chinese smoke query | Proven |
| Product-description understanding and pros/cons | Deterministic source-labelled Match Cards and advice; optional quote-gated `ComparisonEnhancer`; fabricated/unknown evidence rejection tests | Proven |
| Feedback update | Rank-scoped like/reject controls, versioned rejected ASIN state, pre-ranking exclusion, reversal and category-scope reset tests | Proven |
| Selection and structured B/D handoff | Backend-validated shortlist, maximum three products, `selection.v1`, bounded descriptions/bullets/details, evidence rows, comparison summary and explicit finalization | Proven |
| Decision consistency after changes | Selection/rejection/category and same-category requirement changes invalidate finalization; real-catalog smoke proves `finalized → ready_for_comparison` while preserving the shortlist | Proven |
| Local-first execution | Default provider `off`; real-catalog smoke asserts zero tokens and `local_only_no_model`; no inference dependency in `requirements.txt` | Proven |
| Optional local/DeepSeek framework | OpenAI-compatible JSON provider, localhost/plain-HTTP restriction, remote HTTPS boundary, timeout/size limits, evidence gates, fallback and usage tests | Proven without spending a live cloud token |
| Cloud cost/data disclosure | Session, health, browser and audit expose provider/model, `cloud_model`, boundary and disclosure; README documents exactly which shopper/catalog data may leave the machine | Proven |
| Stable A/C and C/D contracts | Detached `get_catalog_product`, `catalog_size`; versioned session/health/chat/selection payloads and stable error codes | Proven |
| Auditable operation | Bounded receipts and unsigned SHA-256 chain; verifier detects edits/reordering; final decision is chained | Proven |
| Runnable HTTP MVP | Zero-dependency server and static UI; real random-port HTTP test covers page, health, session, chat and 413 mapping | Proven |
| Self-contained demonstration | Bundled 15-product synthetic catalog; three tested scripted cases plus interactive terminal and web launchers | Proven |

## Reproduction gates

```powershell
python -m mvp.smoke
python -m mvp.demo --case dress_zh
$env:PYTHONPATH='intent-recognition'; python -m unittest discover -s intent-recognition/tests -q
python -m unittest discover -s shopping_agent/tests -q
python -m unittest discover -s mvp/tests -q
python -m unittest discover -s submission_tools/tests -q
python -m compileall -q agent.py mvp shopping_agent conversation-state-memory intent-recognition submission_tools
git diff --check
```

Current evidence: 92 tests pass (20 intent, 39 integrated Agent, 26 MVP/HTTP/demo,
7 submission), compilation passes, diff check has no errors, and the 50K smoke
finishes with a valid audit chain and zero model tokens.

## Explicit boundaries and limitations

- The reference web process is intentionally single-threaded because A's
  in-memory SQLite FTS connection is construction-thread-bound. Health reports
  this. Production concurrency requires an A-owned thread-safe or per-worker
  index; pretending the current connection is thread-safe would violate the
  module boundary.
- Chinese parsing is deliberately bounded, not general translation. RMB is not
  treated as USD without a validated exchange-rate tool.
- DeepSeek behavior is contract-tested with a fake HTTP opener. A live call is
  unnecessary for local acceptance and would transmit shopper text and spend
  the user's tokens; it requires the user to opt in with `DEEPSEEK_API_KEY`.
- D still owns production hosting, browser/device visual QA and final storefront
  polish. The C-owned static/HTTP integration is tested, but those D deliverables
  are not claimed here.

## Conclusion

The C-owned local MVP is complete against the stated responsibility: it can
advance a shopper from dialogue through versioned requirements, clarification,
real-catalog Top 10 retrieval, evidence-backed product understanding, feedback,
comparison, selection, explicit finalization and a structured/auditable B/D
handoff. Remaining work listed above belongs to the declared A or D boundary or
requires explicit cloud opt-in; it is not hidden as completed C functionality.
