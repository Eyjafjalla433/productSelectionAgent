# Two-stage interface contract

## Data flow

```text
Official message stream
  -> RequirementsCollector
  -> Requirements
  -> Top50CandidateGenerator
  -> CandidateSet[50]
  -> Top10Reranker
  -> RerankResult[10]
  -> official Agent payload
```

## Requirements

`Requirements` is the complete input to candidate generation. It contains only information reconstructed from messages already sent to the Agent:

```python
Requirements(
    category: str,
    hard_constraints: tuple[str, ...],
    soft_preferences: tuple[str, ...],
)
```

The current Agent intentionally discards `user_profile` during `reset()`.

## CandidateSet

CandidateSet is the sole Top50-to-Top10 boundary. Its constructor enforces:

1. Exactly 50 candidates.
2. Unique `parent_asin` values.
3. Continuous ranks 1 through 50.
4. Positive source ranks.
5. Product snapshots limited to title, categories, features, details, description, and store.

No target identifier, ground truth, intent card, private quality tier, selection frequency, or profile tag is accepted by this contract.

## RerankResult

Before the Agent emits recommendations, `validate_against()` enforces:

1. The result belongs to the current CandidateSet.
2. It contains exactly `top_k` candidates.
3. Every ID is unique and belongs to the CandidateSet.
4. Result ranks are continuous from 1 through `top_k`.

The reranker has no catalog handle and cannot expand the candidate pool.

## Replacing one stage

To test another Top50 generator, implement `generate()` and pass it to:

```python
Agent(candidate_generator=my_generator, reranker=existing_reranker)
```

To test another Top10 model, implement `rerank()` and pass it to:

```python
Agent(candidate_generator=existing_generator, reranker=my_reranker)
```

This makes the two metrics independently measurable:

- Top50 candidate coverage diagnoses retrieval.
- Top10 hit rate/MRR diagnoses ranking, bounded by Top50 coverage.

## Leakage boundary

Runtime modules under `techjam_agent/` do not receive evaluator labels. Development scripts may join public labels after inference to calculate metrics, but those scripts are not imported by the Agent.
