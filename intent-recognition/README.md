# Intent Recognition Module

This folder contains the first part of the Shopping Copilot project: intent recognition for conversational shopping search.

The module reads a shopper's message, identifies the user's intent, and extracts product-search attributes for downstream retrieval and multi-turn dialog modules.

## What This Part Does

- Classifies shopping intent as `buying`, `browsing`, or `undetermined`
- Detects override signals such as "actually", "instead", or "ignore my earlier preference"
- Extracts product-related slots, including `category`, `color`, `material`, `size`, `style`, `brand`, `budget`, `feature`, `use_case`, and exclusions
- Separates hard constraints from soft preferences
- Produces keyword and semantic query signals for downstream recall
- Returns ambiguity flags when the message needs more clarification

## Main Files

```text
intent_router/
  router.py                  intent classification and slot extraction logic
  models.py                  result data model
  catalog_lexicon.py         helpers for loading catalog brands and categories

tests/
  test_intent_router.py      unit tests for intent recognition behavior

scripts/
  evaluate_intent_router.py  evaluation helper for public-session messages

evaluator/
  local_evaluator.py         local utilities used by the evaluation helper

docs/
  intent_router.md           English design notes
  intent_router_zh.md        Chinese design notes
  intent_router_handoff.md   handoff notes for teammates
  intent_router_test_results.md
                              recorded test/evaluation summary
```

## Run Unit Tests

From this folder:

```bash
python3 -m unittest discover -s tests
```

Expected result:

```text
Ran 11 tests
OK
```

## Run Public Intent Evaluation

The small public session file is included:

```text
data/public_set.jsonl
```

The large catalog file is intentionally not committed to Git:

```text
data/catalog.jsonl
```

Place the downloaded catalog at `data/catalog.jsonl`, then run:

```bash
python3 scripts/evaluate_intent_router.py
```

The script writes `intent_router_results.json`, which is ignored by Git.

## Output Shape

The router returns an `IntentResult` containing:

- original and normalized query text
- intent type and confidence
- extracted slots
- hard constraints
- filter constraints
- soft preferences
- keyword and semantic query strings
- ambiguity flags
- override detection result
- decision evidence

## Data Attribution

The public data is derived from Amazon Reviews 2023 by McAuley Lab, UCSD. See `DATA_ATTRIBUTION.md` before using or redistributing the data.
