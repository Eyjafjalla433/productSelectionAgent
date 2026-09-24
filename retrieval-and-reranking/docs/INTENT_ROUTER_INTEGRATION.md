# Intent Router integration

## Responsibility boundary

The existing `intent-recognition/` module processes the current message and is
stateless. Its output is an increment, not necessarily the shopper's complete
request.

A state/dialogue component must merge turns, apply overrides, and decide when
the disclosed request is complete. Only then should it construct:

```python
Requirements(
    category=state.category,
    hard_constraints=tuple(state.hard_constraints),
    soft_preferences=tuple(state.soft_preferences),
)
```

and call `Top50CandidateGenerator.generate()`.

```text
IntentResult for each message
          |
          v
State / dialogue accumulation and override handling
          |
          | requirements_complete == true
          v
Requirements
          |
          v
Top50CandidateGenerator -> CandidateSet[50]
          |
          v
Top10Reranker -> RerankResult[10]
```

## Field mapping

| Intent Router output | Accumulated retrieval input |
|---|---|
| `slots.category` or confirmed category | `Requirements.category` |
| `hard_constraints` | merge into `Requirements.hard_constraints` |
| `soft_preferences` | merge into `Requirements.soft_preferences` |
| `override_detected` | remove or replace conflicting earlier state first |
| `intent_type` and `route` | dialogue/retrieval policy signal; not a target label |

Do not turn color, size, material, or feature text into a strict metadata
filter merely because the router extracted a slot. The catalog does not have
uniform structured columns for those fields.

## Current standalone adapter

The packaged `Agent` asks `other` on turns 1 and 2 and ranks on turn 3. This is
a deterministic diagnostic adapter used to test the complete-information
hypothesis. It is not evidence that two `other` questions are sufficient for
the organizer's private sessions.

The final integrated Agent may replace the dialogue collector while leaving
the Top50 and Top10 interfaces unchanged.
