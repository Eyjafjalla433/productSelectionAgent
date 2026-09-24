# User-supplied repository deep dive

Date: 2026-09-14

## Scope and provenance

The five unique repositories supplied by the user were inspected at fixed
snapshots. `Centaurus` appeared twice in the input and was reviewed once.

| Repository | Track / focus | Inspected commit |
|---|---|---|
| `weisintai/tiktok-techjam-2026` | Track 4 conversational product search | `19f4c26` |
| `Deadlycaesar/NiuLai` | Track 4 conversational product search | `1b8e843` |
| `zhanggangyi1224/verbatim-agent` | Track 4 conversational product search | `18966e0` |
| `shrijeet-maiti-burner/needle-techjam-2026` | Track 4 conversational product search | `f2ccfa9` |
| `pranathichijs-ai/Centaurus` | Track 5 synthetic-media detection | `44eec28` |

No repository had a root `LICENSE` file at the inspected snapshot. Therefore the
safe boundary is to learn architectural ideas, measurements and failure modes,
then independently implement them. Source, prompts, copy, assets and result
figures were not copied into this project. Reported scores below are repository
authors' self-reported public-development results, not independently reproduced
results.

## What each repository actually contributes

### Weisintai: evidence-first state mutation

The strongest idea is not the optional LLM. It is the rule that model-extracted
values may update state only when the user's current message contains supporting
catalog evidence. The implementation combines exact phrase/card indexes,
category sets, BM25, exact-facet intersections, a small popularity tie-break,
seen-product exclusion, and candidate-distribution question scoring. It also
keeps the raw user phrase as a soft retrieval query when structured extraction
is uncertain.

Useful ideas to test independently:

- quarantine optional model output behind evidence validation;
- preserve unresolved user language instead of dropping it during extraction;
- rank clarification attributes from the current candidate distribution;
- exclude already emitted products, with an escape hatch for exhausted pools;
- prefer a simple lexical stack when dense retrieval or reranking does not win
  a measured ablation.

Repository-reported headline: TechnicalScore `0.98010`, with a separate
paraphrase stress result of `0.97315`.

### NiuLai: purchase priors and honest product trade-offs

NiuLai layers exact constraint matches and BM25 with lightweight purchase
priors such as review count, price availability and feature completeness. Its
README explicitly distinguishes the highest public-set scoring configuration
from the product behavior the authors preferred to ship. That transparency is
valuable: a benchmark prior can be useful without being mistaken for a general
user-value feature.

Useful ideas to test independently:

- use bounded quality/completeness priors only as tie-breakers;
- report benchmark-optimal and product-preferred policies separately;
- use an optional extractor for paraphrase recall only when it passes an
  evidence check.

Do not adopt its exception fallback that can return `last_ranked`: stale results
after a failed turn violate the current project's fail-closed session contract.
Also treat exact-price bias and an unconditional `other` question as
benchmark-specific hypotheses, not product defaults.

Repository-reported headline: TechnicalScore approximately `0.9466` for the
default configuration.

### Verbatim: invert the simulator's evidence surface

Verbatim constructs normalized, derivable product "cards" and maps each card
back to matching products. Exact evidence intersections dominate; category and
BM25 routes provide fallback. Maximal-span filtering and normalization-invariant
subsequence matching help recover paraphrased evidence. A confidence gate delays
recommendation until exact evidence is sufficiently discriminative, the
intersection is unique, the dialogue budget is exhausted, or a safety turn is
reached.

Useful ideas to test independently:

- build an offline card-to-product inverted index from catalog fields;
- separate exact evidence from merely similar text;
- introduce a measurable confidence gate before shrinking to a final slate;
- test normalization and maximal-span behavior explicitly.

The risk is benchmark over-specialization: deriving the simulator's utterance
surface is powerful on this task but may not transfer to free-form shoppers.
Repository-reported public TechnicalScore is approximately `0.9625`, with lower
scores under progressively harder paraphrase stress.

### Needle: strongest production architecture

Needle has the most reusable engineering patterns. Constraints are immutable
events with polarity, status, turn and `intent_version`. Negation and retraction
are scoped. Derived assets carry a parser fingerprint so stale indexes cannot be
silently paired with changed parsing code. Seen products are keyed by session
and intent version, and only products actually serialized to the user count as
seen. Uncertain textual exclusions remain soft.

Its product-facing "shadow" question board is especially useful. It scores
candidate-grounded facets by expected residual set size, offered-option coverage
and interaction cost. Unknown or unoffered values are kept as an explicit
remainder, preventing a high-cardinality but unanswerable facet from looking
artificially valuable. The board is isolated from the benchmark-facing action.

Useful ideas to test independently:

- bind generated assets to code/config fingerprints;
- version shown-product memory by user intent;
- count only successfully emitted products as shown;
- keep uncertain exclusions soft;
- separate a product-facing question policy from the scored compatibility
  policy, with both made observable;
- retain faithful, target-blind traces and bounded degraded responses.

Repository-reported TechnicalScore is approximately `0.9785` in its documented
configuration.

### Centaurus: robustness discipline, not a shopping algorithm

Centaurus is a Track 5 media-forensics project. Its transferable value is its
evaluation frame: train a simple frozen-backbone classifier, stress it with JPEG,
blur, noise and crop transformations, then evaluate on an unseen generator
distribution. The authors disclose that the held-out WildFake result falls near
chance and that wrong predictions can remain highly confident.

For this project, translate that into catalog-disjoint, paraphrase, noisy-input
and schema-drift suites, plus confidence calibration. Do not transplant its model
code: the inspected repository also contains reproducibility gaps, including a
placeholder loader and presentation-oriented hard-coded result data.

## Changes adopted now

Nine ideas were independently implemented in the web MVP without changing the
official Agent or its score-facing behavior:

1. **Non-operative shadow question board.** After real retrieval, candidate
   facets are scored by offered-option coverage, expected candidate-set
   reduction and remaining-turn interaction cost. Unknown/unoffered values form
   a remainder group. The board is explicitly labeled non-operative.
2. **State provenance.** Each visible hard or soft constraint shows the bounded
   user evidence and source turn that created it.
3. **Turn diff.** The lens reports added, updated and removed state slots with
   their before/after values.
4. **Intent-scoped novelty receipt.** Catalog IDs count as shown only after they
   are returned to the UI. A category/intent switch opens a fresh novelty scope,
   so repeat diagnostics from an old shopping task do not leak into a new one.
5. **Local shortlist.** A reviewer can hold up to three products for a compact
   comparison. This interaction remains browser-local and is labeled as such;
   it cannot covertly affect state or ranking.
6. **Bounded audit export.** The demo keeps a ten-turn user-visible receipt log
   and exports it locally without full candidate pools or hidden labels. An
   unsigned SHA-256 chain detects edited or reordered turns while explicitly
   avoiding a false claim of authenticated provenance. A zero-dependency CLI
   verifies the schema, turn order, links, per-turn digests and final chain head.
7. **Guided demo stories.** Each scenario exposes its three-turn script in the
   UI. Prompts are loaded, never auto-submitted, keeping the reviewer in control
   while making multi-turn behavior reproducible.
8. **Structured Match Cards.** Every displayed product reports hard and soft
   evidence separately, including unknown or conflicting catalog facts. Strict
   category checks require taxonomy evidence when taxonomy is available, which
   prevents a material phrase such as “jersey pajama” from impersonating a
   product category.
9. **Intent-scoped fresh slates.** Adaptive retrieval excludes only product IDs
   actually emitted in the current intent. A category switch clears that scope;
   an exhausted pool explicitly falls back to reuse rather than hiding the only
   valid matches or inventing results.

Implementation locations:

- `mvp/shadow_policy.py`
- `mvp/audit.py`
- `mvp/explanations.py`
- `mvp/server.py`
- `mvp/static/index.html`
- `mvp/static/styles.css`
- `mvp/static/app.js`
- `mvp/tests/test_server.py`

The existing fail-closed behavior was deliberately retained: an exception never
replays stale recommendations.

After adding `jersey`/sports intent coverage and suppressing generic result
commands as pending-category answers, the unchanged official public-200
evaluation reproduced the prior baseline exactly: Hit@10 `0.985`, MRR
`0.888375`, MTTC `3.205`, TechnicalScore `0.914913`, zero reported tokens and
zero runtime errors. The stricter taxonomy gate is used by adaptive retrieval;
the score-compatible official path remains available and unchanged.
The same public-200 metrics were reproduced again after intent-scoped novelty
was added to the shared state, confirming that the score-compatible output did
not regress.

## Prioritized experiments

These should be gated by ablations; none should silently replace the submitted
default.

| Priority | Experiment | Success condition | Main risk |
|---:|---|---|---|
| 1 | Intent-versioned shown-set exclusion | fewer repeated slates without lower Hit@10 | hiding the only relevant item |
| 2 | Raw-query fallback when extraction is sparse | better paraphrase recall with stable precision | noisy terms dominate BM25 |
| 3 | Exact evidence-card inverted index | higher target rank on explicit evidence | simulator overfitting |
| 4 | Confidence gate and dynamic slate size | better product experience at stable score | extra clarification turns |
| 5 | Bounded completeness/popularity tie-break | improved MRR without subgroup regressions | popularity feedback loop |
| 6 | Robustness and calibration matrix | lower worst-slice error and calibrated confidence | optimizing only reported slices |

Minimum evaluation matrix:

- official public 200 sessions;
- paraphrase tiers with unchanged intent;
- catalog-disjoint or category-held-out sessions;
- noisy punctuation, misspelling and short-answer sessions;
- schema-missing fields and empty-price cases;
- repeated-turn and mid-session intent-switch cases;
- latency, memory, token usage and stale-result invariants.

## Bottom line

The best synthesis is: Weisintai's evidence boundary, Verbatim's exact evidence
index, Needle's versioned state and shadow policy, NiuLai's measured lightweight
priors, and Centaurus's OOD honesty. The product should keep a clean line between
diagnostics/experiments and the official deterministic path. That yields a more
credible agent rather than a collage of leaderboard tricks.
