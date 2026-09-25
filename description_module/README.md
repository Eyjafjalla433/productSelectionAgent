# Product Description and Comparison

This module explains and compares up to **three products already selected by the user**. It keeps objective product facts separate from personalized advice and returns structured data for the Agent and Frontend modules.

## Where it fits

```text
Search/Ranking finds products
    → Agent displays the results
    → User selects up to three products
    → Description/Comparison processes the selection
    → Agent and Frontend receive the result
```

The module preserves the selection order. It does not search the full dataset or change the Search/Ranking results.

## Processing steps

1. **Choose an attribute schema.** Use the category supplied by the Agent. If absent, let the extraction model identify it from product text. Unknown categories use a general schema.
2. **Extract attributes.** Read titles, descriptions, bullet points and details. Extract common, category-specific and additional discovered attributes.
3. **Compare objectively.** Build an attribute table and generate pros, cons and trade-offs without user preferences. Missing information remains unknown.
4. **Compare personally.** Add user requirements to explain suitability. Keep the objective comparison unchanged and do not reorder products.

With a model provider, this normally uses three model calls. Personalization is skipped when `hard`, `soft` and `excluded` are all empty. Generated explanations are requested in English; source quotes retain their original wording.

## Input from the Agent

Pass the Agent's selection handoff. This is a synthetic example:

```python
handoff = {
    "schema_version": "show-me-your-agent.selection.v1",
    "session_id": "example-session",
    "intent_version": 1,
    "requirements": {
        "state_version": 1,
        "hard": {"category": "tshirt"},
        "soft": {"material": ["cotton"]},
        "excluded": {}
    },
    "selected_products": [
        {
            "parent_asin": "EXAMPLE-A",
            "title": "Cotton T-shirt",
            "product_description": ["A cotton T-shirt with a relaxed fit."],
            "product_bullet_points": ["Machine washable"],
            "details": {"Brand": "Example Brand", "Color": "Black"},
            "price": None,
            "average_rating": None
        }
    ]
}
```

- `hard`: requirements the user specifies as necessary.
- `soft`: user preferences.
- `excluded`: features the user wants to avoid.
- `parent_asin`: the product ID used by the Agent. IDs must be unique, nonempty strings.

An empty selection returns `empty`. More than three products, duplicate IDs or an unsupported input schema raise `ValueError`.

Category is read from `requirements.hard.category`. It guides schema selection and is not proof of a product's actual category. Current schemas cover T-shirts, clothing, shoes, laptops, headphones and general products.

Full descriptions are optional. When unavailable, the module uses supplied titles, bullet points and details and returns a warning. It does not fetch missing descriptions itself. The Search/Ranking and Agent modules must pass additional product information through to this input.

## How to call it

Requires **Python 3.10+**. The module itself has no third-party dependencies.

Run from the repository root:

```python
from description_module import DescriptionComparison

# Use the model provider configured by the Agent module.
comparison = DescriptionComparison(provider)
result = comparison.compare(handoff).to_dict()
```

`result` is a Python dictionary. The backend can convert it to JSON for the Frontend; no separate JSON file is required for each request.

The provider must expose `name`, `model`, and this method:

```python
provider.complete_json(system=..., user=..., max_tokens=...)
```

The method returns an object with `data` (a parsed JSON dictionary) and `usage` (containing `prompt_tokens` and `completion_tokens`). This matches the existing Agent model-provider interface. Configure credentials through the provider, not in this module.

To connect to the existing Agent runtime:

```python
runtime.comparison_enhancer = DescriptionComparison(provider)
handoff = runtime.selection_handoff(session_id)
result = handoff["comparison_assist"]
```

The runtime calls the module and manages its existing comparison cache. Do not run a second comparison manually after this handoff. Keep the runtime's model-provider and cloud-use settings consistent with the supplied provider.

## Output for the Frontend

The output schema is `description-comparison.v1`.

| Field | What it contains |
|---|---|
| `selected_asins` | Product IDs in the original selection order |
| `product_profiles` | Extracted attributes, category and supporting quotes |
| `objective_comparison.comparison_matrix` | Attribute table; rows contain `dimension` and `values` keyed by product ID |
| `objective_comparison.product_assessments` | Objective pros and cons |
| `objective_comparison.trade_offs` | Trade-offs between products |
| `objective_comparison.unknowns` | Missing attributes |
| `personalized_comparison.products` | Per-product `fit_reasons` and `cautions` |
| `products` | Simplified objective pros and cons for the existing shortlist UI |
| `status`, `warning` | Processing status and missing-data or model-error messages |
| `usage`, `latency_ms`, `provider`, `model` | Model call information |

Each simplified pro or con contains `text`, `evidence` and `source`. Full comparison points contain `evidence_refs` identifying the product, source field and quoted text.

Display objective comparison and personalized advice separately. Show missing values as **Unknown**, and label inferred attributes as inferences. The current shortlist UI can read the simplified `products` field; the full attribute table and personalized advice need additional Frontend rendering.

Status values:

- `empty`: no selected products.
- `offline_preview`: no provider supplied; only direct catalog facts and the output structure are produced.
- `partial`: a model stage failed; available results are retained.
- `completed`: model stages returned without a caught error. This does not certify factual accuracy or guarantee every field is populated.

## Demo and tests

From the repository root:

```sh
python -B -m description_module.demo
python -B -m unittest discover -s description_module/tests -v
```

The demo uses synthetic products without a model provider. It demonstrates the data structure, not full AI-generated comparisons.

Eight automated tests passed using simulated model responses. A compatibility check also passed with the existing Agent runtime's handoff and cache using a simulated catalog. **Real model calls and comparison quality on real products have not yet been tested.**

The module checks that evidence quotes occur in the corresponding product fields. This helps reject fabricated quotes, but does not prove that every interpretation is correct. Real-model evaluation is still needed.

## Files

- `pipeline.py`: input checks, extraction, comparisons and output formatting.
- `__init__.py`: public Python import entry.
- `demo.py`: offline example.
- `tests/test_pipeline.py`: automated checks.
