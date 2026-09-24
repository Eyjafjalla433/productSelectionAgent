# C — Agent and backend integration contract

This document describes the product-facing MVP boundary owned by C. It keeps
retrieval quality owned by A, comparison/explanation owned by B, and customer
experience/deployment owned by D independently replaceable.

## Runtime flow

```text
POST /api/chat
  -> validate session and sequential turn
  -> deterministic intent parsing
  -> optional evidence-gated model extraction
  -> versioned requirement state update
  -> pre-retrieval clarify/retrieve policy
  -> A: candidate retrieval
  -> bounded retry without relaxing hard constraints
  -> ranking and post-retrieval policy
  -> record actual question + shown product IDs
  -> B: product Match Cards / pros and cons
  -> bounded receipt + audit-chain append
  -> D: render conversation, state, Top 10 and shortlist
  -> validate selection -> structured B/D handoff
```

The orchestrator accepts at most ten products and never mutates A's catalog
index. UI wording is not embedded in retrieval or ranking logic.

C does not reach into A's retriever/index implementation. The integrated Agent
exposes two read-only capabilities: `catalog_size` for health/acceptance checks
and `get_catalog_product(parent_asin)` for presentation and B/D handoff. Product
records are detached copies, so C or D cannot mutate A's in-memory catalog by
editing a response. Unknown IDs return `None`; selection still requires an ID
that was actually shown in the current session.

All C-owned HTTP payload families carry an explicit version rather than relying
on endpoint names as an implicit contract: `session.v1`, `health.v1`, `chat.v1`,
`selection-state.v1`, `selection.v1` for the B/D handoff, and the audit schema
defined by `mvp.audit`. Additive fields remain backward-compatible within v1;
removing or changing field meaning requires a new version.
`POST /api/product` returns `product-detail.v1` only for an ASIN already shown in
that session. It exposes bounded catalog descriptions, bullet points, details,
the last observed match and deterministic advice; it does not retrieve, rerank,
advance the turn counter or invoke a model.
HTTP errors preserve the readable `error` string and also include a stable
`error_code` such as `session_expired`, `turn_limit`, `product_not_shown`,
`selection_limit`, or `message_too_long`; D must branch on the code rather than
parse English copy. Unexpected failures use `internal_error` without exposing a
stack trace or credential-bearing exception text.

The deterministic parser has a bounded bilingual adapter for common Chinese
categories, colors, materials, exclusions and USD budget phrases. Canonical
English values enter A's existing retrieval contract. Unicode model evidence is
validated without transliteration. RMB amounts are intentionally not converted
or applied to the USD catalog until a separately validated exchange-rate tool
exists.
Chinese input also sets a session-level presentation locale, so subsequent short
answers and selection controls remain Chinese. Localization consumes structured
policy reasons in the web layer; it does not place translated UI text in A's
retriever/ranker or alter B's evidence.

## Session and turn guarantees

- `POST /api/session` creates an isolated in-memory session and returns turn 1,
  the ten-turn ceiling, runtime/provider/model names, guided demo stories and a
  machine-readable model data boundary. `cloud_model`, `data_boundary` and
  `data_disclosure` distinguish zero-token offline mode, a localhost endpoint
  and an external endpoint. The same disclosure appears in `/api/health`, the
  browser UI and exported audit metadata.
- Idle sessions expire after 3,600 seconds by default and the service retains at
  most 128. Expiration/capacity eviction removes both HTTP session data and all
  underlying Agent memory fragments, traces and session-scoped diagnostic
  errors. `--session-ttl` and `--max-sessions` configure these limits;
  `/api/health` reports their effective values. C retrieves a completed trace by
  `(session_id, turn)` rather than assuming that a shared trace list's final row
  belongs to the current request.
- The reference HTTP server is intentionally single-threaded because A's current
  in-memory SQLite FTS connection is construction-thread-bound. Health exposes
  `concurrency_mode=single_threaded_sqlite_owner`. D must not advertise this
  demo process as a concurrent production service; a threaded/multi-worker
  deployment first requires an A-owned thread-safe or per-worker index.
- Turns are strictly sequential. A replay with identical inputs is idempotent at
  the Agent boundary; conflicting replay is rejected.
- Chat messages are capped at 1,000 characters by the backend as well as the UI,
  bounding parser/model payloads and preventing API clients from bypassing the
  browser's token-cost guardrail. Oversized messages fail with HTTP 413 before
  state mutation or a model request.
- State holds hard constraints, decaying soft preferences, exclusions, evidence,
  asked questions and shown IDs. A category change starts a new intent version.
- Execution feedback records only the question actually asked and the products
  actually shown. Errors fail closed and do not reuse stale candidates.
- Explicit like/reject commands resolve ranks only against the latest displayed
  set. Rejected ASINs are stored in versioned state and passed to A as a filter,
  not a C-layer score adjustment. They remain excluded during novelty fallback;
  an explicit keep/select reverses the feedback. Category changes clear the old
  product-feedback scope.
- Explicit shortlist/compare/remove/finalize chat commands become auditable
  `0_control` turns. They preserve strict turn ordering while bypassing requirement
  mutation, retrieval, ranking and model usage.
- Already shown product IDs are excluded within an intent when a fresh eligible
  pool exists. Exhaustion is explicit and may safely reuse earlier products.
- A category/intent reset also clears the selection, preventing an old-category
  product from being handed off under a new requirement state.

## Optional model helper

The default is `--model-provider off`: no API call and zero model tokens. The
`local` and `deepseek` modes use a narrow OpenAI-compatible JSON endpoint. The
model is only an extraction helper; deterministic parsing, state validation,
retrieval and ranking remain authoritative.

Every proposed update must satisfy all gates:

1. allowed shopping slot and `set`/`exclude` operation;
2. value and evidence occur in the current user message;
3. an exclusion has explicit negative wording;
4. deterministic slots cannot be overwritten;
5. at most six updates are accepted.

Timeouts, invalid JSON, truncation and provider errors produce no model updates
and continue through the deterministic pipeline. API keys are read from process
environment only and are never placed in responses, logs or audit exports.

An explicitly requested comparison may also call the provider once with only
the current requirements and up to three selected products' bounded catalog
fields. Returned product IDs must be selected IDs, and every accepted pro/con
must include an exact quote from that same product. These are labelled drafts;
they cannot update requirements, scores or ranking. Results are cached by intent,
state version and selected IDs. `usage_this_call` is zero on a cache hit, while
`usage` preserves the original call's accounting. DeepSeek mode therefore sends
both normal shopper messages and, on compare/export, selected catalog excerpts
to the configured cloud endpoint; local/off modes keep that data local.

## HTTP handoff

`POST /api/chat` input:

```json
{"session_id":"...","message":"I need a blue dress under $80"}
```

After results exist, the same endpoint accepts conservative selection controls,
for example `Compare #1 and #3`, `Remove the second one`, `Clear shortlist`,
`选择第一个和第三个`, or `完成选择`. Rank references always resolve against the
latest displayed result set; unavailable ranks are reported rather than guessed.

The response contains the Agent message, next turn, up to ten enriched products,
and a bounded receipt. Important receipt fields are:

- `hard`, `soft`, `excluded`, `state_evidence`, `state_changes`;
- `rejected_asins` for explicit product-level feedback;
- `candidate_count`, `shown_count`, `new_product_count`, `repeat_count`;
- `pre_action`, `post_action`, `shadow_questions`, `result_quality`;
- `model_assist` and `model_usage` when the optional helper ran;
- stage timings, without the full candidate pool or hidden evaluation labels.

`POST /api/audit` exports user-visible turns in an unsigned SHA-256 chain. This
detects accidental edits/reordering; it does not authenticate a person.

`POST /api/select` accepts only catalog IDs that were actually shown in that
session and enforces a three-product limit. Selection is intentionally outside
the ranker, so choosing an item cannot silently change later scores.

`POST /api/handoff` emits `show-me-your-agent.selection.v1`. Each selected item
contains bounded raw `product_description`, `product_bullet_points`, product
details, the last observed rank/score, requirement-match signals and grouped
supported/conflicting/unknown evidence. The MVP also emits source-labelled,
extractive fit/watch-out points as a deterministic fallback. B can improve the
comparative prose from these
facts; D can render the result without recomputing rank or hard constraints.
`comparison_summary.rows` aligns price, rating, hard coverage and evidence counts
by ASIN. Lowest-price/highest-rating IDs are descriptive catalog facts and are
explicitly not a reranking decision.

Exporting a handoff produces a `draft` or `ready_for_comparison` artifact; it is
not consent to a final choice. An explicit finalize control changes the status to
`finalized` and records UTC time, conversation turn and ordered ASINs in
`decision`. Later selection/product-feedback/category changes invalidate that
status. Any same-category requirement change (for example color, material,
budget or exclusion) also invalidates finalization while retaining the shortlist
for re-review. `selection_state` mirrors the status and finalization metadata so D can
render it without re-deriving backend state. The final decision is also copied
into the chained turn audit.

## Run and verify

```powershell
python -m mvp.server
python -m unittest discover -s shopping_agent/tests -v
python -m unittest discover -s mvp/tests -v
python -m unittest discover -s submission_tools/tests -v
python -m submission_tools.abcd_evaluate --product-sessions 12
```

Optional provider examples are documented in the root README. A live DeepSeek
test is deliberately not part of automated verification because it would send
shopper text externally and consume the user's API quota.
