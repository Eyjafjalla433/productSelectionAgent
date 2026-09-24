# Competition Specification

## Objective

Build a multi-turn shopping agent that finds a hidden target product as early and as highly ranked as possible.

The hidden target is based on a real purchase record from Amazon Reviews 2023. Customer messages are simulated from a hidden intent card derived from product metadata; the source dataset does not contain real shopping conversations.

## Scope

In scope: keyword, dense, or hybrid retrieval; Buying/Browsing routing; query rewriting; semantic reranking; conversation-state management; clarification strategy; anonymized-profile use; legally accessible LLM APIs or local models.

Out of scope: catalog modification, identifiers outside the frozen catalog, private-label reconstruction, real transactions, mandatory UI work, full-model training, multimodal systems, and infrastructure-heavy vector databases.

## Official Data

The frozen `Clothing_Shoes_and_Jewelry` catalog contains 50,000 products. Participant-visible fields are `parent_asin`, `title`, `features`, `description`, `price`, `categories`, `details`, `average_rating`, `rating_number`, and `store`. Only `parent_asin` is scored.

The public set has 200 labeled development sessions. The organizer keeps 800 sessions private. Private intent cards, ground truth, and simulator state are never sent to the participant Agent.

Direct user identifiers, purchase timestamps, free-text reviews, and raw purchase histories have been removed. The Agent sees only a safe aggregate `user_profile` with purchase-frequency and rating summaries plus controlled preference tags.

Both splits use the same fixed scenario mix:

- 40% Buying: a hard constraint is disclosed early.
- 40% Browsing: the customer begins vague.
- 15% Intent Override: an earlier preference is replaced on turn 3 or 4.
- 5% Boundary: the customer may have no preference for a requested attribute.

## Session Protocol

1. The evaluator creates a random `session_id` and calls `reset(session_id, user_profile)`.
2. The simulated customer sends a scenario-dependent first message.
3. The Agent returns natural `message`, structured `ask_attribute`, and ranked `recommendations`.
4. The evaluator scores the first 10 unique catalog-valid `parent_asin` values.
5. A target hit records rank and turn; otherwise the deterministic customer policy replies.
6. An Intent Override session cannot convert before the new intent is sent.
7. The session ends after a valid hit or turn 10.

The simulator policy decides what information to reveal. If natural-language paraphrasing is added by the organizer, it cannot decide correctness. Hits are always exact code matches.

## Required Agent Interface

```python
class Agent:
    def reset(self, session_id: str, user_profile: dict) -> None:
        pass

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        return {
            "message": "Do you have a material preference?",
            "ask_attribute": "material",
            "recommendations": [{"parent_asin": "B000..."}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30}
        }
```

Rules:

- `message` is customer-facing natural language.
- `ask_attribute` is one allowed attribute or `null`; the simulator uses this field instead of guessing from prose.
- Recommendations are ordered best to worst. Invalid and duplicate IDs are removed; only the first 10 valid unique IDs are scored.
- An optional numeric recommendation `score` is accepted but ignored.
- `usage` reports non-negative prompt and completion token counts. It is optional when no model is used.
- Exceptions, invalid output, and timeouts may count as a miss.

## Metrics

```text
HitRate@10 = successful sessions / N
MRR = sum(1 / target_rank, with misses equal to 0) / N
MTTC = sum(first_hit_turn, with misses assigned 11) / N
Efficiency = clip((11 - MTTC) / 10, 0, 1)
TechnicalScore = 0.50 × HitRate@10 + 0.30 × MRR + 0.20 × Efficiency
```

The same metrics are reported separately for Buying, Browsing, Intent Override, and Boundary sessions. Reported token use and latency are feasibility measures and do not change the core score.

## Innovation Directions

- Buying versus Browsing routing and multi-route retrieval
- hybrid retrieval and semantic reranking
- structured constraint state, intent override handling, and dynamic context construction
- adaptive clarification and question-value estimation
- safe personalization using the aggregate profile
- failure detection, strategy switching, low latency, and low token cost
- transparent recommendation explanations

## Model and API Policy

Teams choose and manage their own model credentials. API keys must be passed through environment variables and never committed. Teams disclose model choice, approximate cost, token usage, latency, and any fallback behavior. The organizer does not need to issue a common API key.

## Final Deliverables

- Source code with setup and reproduction instructions
- A working Agent using the required interface
- A short report covering architecture, models, cost, limitations, and team contributions
- One demonstrated multi-turn session
