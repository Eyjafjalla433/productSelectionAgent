# Shopping Copilot — technical report

## Problem and scope

A shopper reveals and changes requirements over at most ten turns. The agent
must find a hidden target in a frozen catalog without receiving its identity.
Our submission is `agent.Agent`, connecting intent, state, retrieval and ranking
through explicit, versioned interfaces. It uses Python's standard library and
SQLite FTS5 on CPU. No Dense/LLM inference, GPU, external vector database, model
training or credentials are active. This is a bounded implementation of the
track goals, not a claim of full semantic or persistent-learning capability.

## Method

1. Intent produces explicit set, clear, exclude and remove-exclusion operations.
   Pending questions help interpret short replies; state does not parse the
   same original sentence independently.
2. Detached snapshots retain hard requirements, decaying soft preferences,
   exclusions, weak profile hints, a compact summary, asked questions and shown
   products. Category changes remove stale product-specific conditions.
3. The submitted pre-retrieval policy performs two evidence-collection turns;
   question content is selected from current State. From turn three onward,
   post-retrieval quality and feedback may trigger one additional clarification.
4. The resulting State feeds module 2's deterministic keyword, category and catalog-evidence routes.
   Its recall-compatible Top-50 boundary favors coverage and reproduces module
   2/4's candidate structure. The alternative adaptive research profile performs
   stricter lexical checks, dynamic fusion and variable truncation.
5. Existing locked CPU reranking combines reciprocal-rank route features. At most ten IDs are
   returned. Empty/broad pools or negative feedback may trigger post-retrieval
   clarification. Rank-derived scores are not probabilities.
6. Only actual questions and displayed IDs enter feedback. Two warm-up questions
   plus at most one dynamic post-retrieval question is our heuristic, distinct
   from the ten-turn rule. One retry may
   broaden lexical depth and remove soft query terms, never hard constraints.
   Identity/version checks reject stale results; errors return no old candidates.

CPU browsing changes lexical weights; it does not implement Dense semantic
retrieval or guarantee cross-category diversity. The aggregate profile is an
input supplied by the organizer, not a model learned from private histories.

## Evaluation

We use all 200 official public sessions, the frozen 50,000-product catalog and
the unmodified organizer evaluator. No synthetic proxy data is used for these
results. Ground truth and simulator state remain outside the Agent. This public
set was used during development and is not an independent generalization test.

| Configuration | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---:|---:|---:|---:|---:|
| **Submitted fixed-warm-up + dynamic-4B profile** | **0.985000** | **0.888375** | **3.205** | **0.779500** | **0.914913** |
| Earlier state-evidence integration | 0.985000 | 0.879208 | 3.095 | 0.790500 | 0.914362 |
| Earlier adaptive strict integration | 0.970000 | 0.575508 | 2.255 | 0.874500 | 0.832552 |
| Earlier official BM25 starter run | 0.125000 | 0.068034 | 9.810 | 0.119000 | 0.106710 |
| Earlier fixed-two-question locked+lite baseline | 0.985000 | 0.884625 | 3.205 | 0.779500 | 0.913788 |

Historical baselines use the same frozen public data but differ in multiple
pipeline choices. Against the component-4 baseline, the submitted integration
preserves Hit@10 and MTTC, gains 0.003750 MRR and gains 0.001125
TechnicalScore. This is a public-set development result rather than proof of
private-set generalization.
TechnicalScore is the evaluator's metric, not the overall event judging score.

| Scenario | Sessions | Hit@10 | MRR | MTTC |
|---|---:|---:|---:|---:|
| Buying | 80 | 0.9875 | 0.913854 | 3.100000 |
| Browsing | 80 | 1.0000 | 0.904167 | 3.000000 |
| Intent override | 30 | 0.966667 | 0.791111 | 3.833333 |
| Boundary | 10 | 0.9000 | 0.850000 | 3.800000 |

Full sample outcomes and source/data hashes: [public200.json](public200.json).
Clean environment and archive checks: [reproduction.json](reproduction.json).

## Runtime and model disclosure

Reference: CPython 3.12.13 on macOS arm64; standard library only, with SQLite
FTS5. The package is tested in a clean directory and fresh virtual environment.
The offline evaluation check blocks socket connections/DNS. Official data is
installed separately from a hash-pinned ZIP; the submitted source package does
not contain catalog data, labels or model weights.

The report's `runtime` section discloses initialization time, evaluation time,
respond-call mean/P50/P95/max latency, and peak process RSS. RSS includes the
evaluator and its catalog map, not just the agent. Timings depend on hardware
and concurrent load. Model token usage and API cost are zero because no model
is invoked; local hardware/electricity costs are not estimated.

Measured clean-package run (macOS arm64, CPython 3.12.13):

| Measurement | Value |
|---|---:|
| Agent initialization | 5.118 s |
| Official evaluation loop | 7.769 s |
| Mean respond latency | 12.133 ms |
| P50 respond latency | 3.680 ms |
| P95 respond latency | 40.020 ms |
| Maximum respond latency | 151.669 ms |
| Peak process RSS, including evaluator | 1386.1 MiB |

Importable model helper classes do not mean a model was loaded. Dense failure
handling has a simulated test, not a claim of real model validation. Final
scoring does not depend on external services or live credentials.

## Validation and limitations

The development repository passed all 120 module/integration/submission tests.
Six additional wrapper/setup tests cover the official export, catalog override,
missing-data handling, unchanged catalog, ten-turn limit and wrong-archive
rejection. The package carries 29 integration/submission tests; broader module
regression evidence is recorded separately.

Limitations include narrow English parsing, some unknown buying intents,
lexical attribute checks, ambiguous variants, unknown-price recall loss, a fixed
question budget and no persistent profile learning. Mentioned colors or missing
material words do not certify variant availability or physical composition.
The system does not perform purchases or guarantee inventory.

The real-catalog override demo exposes category false positives: "dress" can
match dress shirts, and the broad "Clothing, Shoes & Jewelry" taxonomy can let
non-shoe products pass a shoes query. Those demos validate state operations,
not semantic correctness of every recommendation. Leaf-category normalization
and disambiguation remain needed before claiming precise category filtering.

Next steps are controlled clarification/ranking ablations, better attribute and
negation extraction, and separately verified semantic extensions with a CPU
fallback. See [CONTRIBUTIONS.md](CONTRIBUTIONS.md) for responsibility allocation.
