# Intent Understanding

`intent_router.IntentRouter` is the team-one current-turn understanding component. It does not retrieve products, choose retrieval routes, mutate session state, rank candidates, or choose clarification questions. It parses only what the user explicitly says, so a use case such as `for hiking` never invents attributes such as `waterproof`.

## Initialization and use

```python
from intent_router import IntentRouter, load_catalog_brands, load_catalog_categories

router = IntentRouter(
    known_brands=load_catalog_brands("data/catalog.jsonl"),
    known_categories=load_catalog_categories("data/catalog.jsonl"),
)
intent = router.understand(user_message)
```

The catalog-backed lexicons are loaded from the real `store` and `categories` fields and must be cached during Agent initialization.

## IntentResult contract

| Field | Meaning |
| --- | --- |
| `intent_type` | `buying`, `browsing`, or `None` for an incomplete current-turn intent. |
| `intent_confidence` | Deterministic confidence in the current-turn intent decision. |
| `slots` | Explicitly mentioned category, brand, color, material, budget, size, style, audience, use case, feature, and exclusion slots. |
| `hard_constraints` | Explicit non-negotiable user constraints; these are not automatically metadata filters. |
| `filter_constraints` | Only hard constraints backed by fixed catalog metadata: category, brand/store, and price bounds. |
| `soft_preferences` | Semantic retrieval and ranking preferences. |
| `keyword_query`, `semantic_query` | Query rewrites for downstream retrieval. |
| `override_detected` | Signal for State & Memory to replace stale values. |
| `ambiguity_flags`, `decision_evidence` | Diagnostics for policy and debugging. |

The Router is deliberately not a keyword binary classifier. Budget, category, slot count, and `I want` are weak signals. Strong purchase-commitment phrases produce `buying`; strong exploration phrases produce `browsing`. An incomplete query can have `intent_type=None` while still returning slots and constraints for downstream modules.

## Constraint semantics and catalog alignment

The frozen catalog exposes `parent_asin`, `title`, `features`, `description`, `price`, `categories`, `details`, `average_rating`, `rating_number`, and `store`. It has no guaranteed standalone material, color, size, or feature columns.

- Price bounds, exact brand/store constraints, and confirmed Buying category constraints can enter `filter_constraints`.
- Material, color, size, feature, and exclusion constraints remain in `hard_constraints` or `soft_preferences` and must be handled through text or semantic matching unless Retrieval adds a validated parser.

## Official Agent compatibility

`IntentResult` is an internal hand-off object, not the public Agent response. The integration layer must return the official `message`, `ask_attribute`, and up-to-10 `parent_asin` recommendations. The only valid `ask_attribute` values are `category`, `material`, `color`, `size`, `style`, `brand`, `budget`, `feature`, `use_case`, `other`, and `null`.

## Evaluation alignment

The public JSONL contains session metadata and targets, not static user queries. `scripts/evaluate_intent_router.py` materializes official evaluator messages from public target metadata for local Router checks. This validates simulator alignment, not open-ended natural-language generalization.

```bash
python3 -m unittest tests.test_intent_router -v
python3 scripts/evaluate_intent_router.py
```
