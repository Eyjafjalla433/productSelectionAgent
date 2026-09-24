# Shopping Copilot: Stateful Conversational Product Search

> A CPU-only shopping agent that remembers changing requirements, knows when to ask, and searches 50,000 catalog products without a hosted model or external API.

## Inspiration

Online shopping rarely begins with a perfect query. A shopper may start with *“I need a black dress under $50,”* change the color one turn later, remove the budget, and then switch to shoes. A conventional keyword search treats each message independently. A conversational assistant can fail in a different way: it may remember too much, carry obsolete constraints forward, or ask generic questions that do not help the shopper converge.

That gap inspired **Shopping Copilot**. We wanted to build an agent whose reasoning is visible in its behavior: understand the current intent, maintain a structured view of what still matters, decide whether the available context is sufficient, retrieve and rank real catalog products, and revise the workflow when the shopper changes direction.

## What it does

Shopping Copilot runs against the organizer's frozen catalog of **50,000 Clothing, Shoes & Jewelry products**. During a conversation, it:

- distinguishes targeted buying from open-ended browsing;
- extracts hard constraints such as category and maximum price;
- retains decaying soft preferences such as color, material, style, and use case;
- supports explicit set, clear, exclude, and remove-exclusion operations;
- clears stale product-specific conditions after an intent or category override;
- asks for missing information before retrieval when necessary;
- combines keyword, category, and catalog-evidence retrieval routes;
- reranks candidates and decides whether to recommend or clarify;
- returns at most ten valid catalog IDs; and
- enforces the competition's ten-turn session limit.

The submitted policy uses **two state-informed evidence-collection turns**. From turn three onward, candidate quality and user feedback drive a dynamic post-retrieval policy. For example, if the user rejects the first results, the agent can ask for one additional feature and then retrieve and rank again.

## How we built it

We integrated five project responsibilities through explicit, versioned interfaces:

```text
User
  → Intent Router / Query Understanding
  → Conversation State and Context
  → Pre-retrieval Policy (4A)
  → Multi-route Retrieval
  → Ranking and Post-retrieval Policy (4B)
  → Recommend or Clarify
  → State Feedback
```

The intent layer turns language into structured state operations instead of passing loosely interpreted text between modules. A single state manager applies those operations and produces a detached snapshot containing hard constraints, soft preferences, exclusions, profile hints, question history, and shown products. Session, turn, and state versions prevent stale or cross-session handoffs.

For retrieval, the submitted offline profile uses **SQLite FTS5/BM25**, category evidence, catalog evidence, and reciprocal-rank features. It preserves a recall-compatible Top-50 candidate boundary before a deterministic CPU reranker returns the final Top-10. A stricter adaptive profile remains available for ablation experiments, but it is not silently substituted for the submitted configuration.

The entire submitted runtime uses Python's standard library and SQLite FTS5. It requires no GPU, model weights, paid API, credentials, external vector database, or network connection during inference. We also built reproducible setup, hash verification, evaluation, tracing, packaging, and demo commands around the official `Agent` interface.

## Challenges we faced

### 1. Integration was a semantic problem

The hardest failures were not syntax errors. Two independently developed modules could parse the same sentence differently, or use the same field name with different meanings. An exclusion could accidentally become a positive preference, and a short answer such as *“blue instead”* could be interpreted without the question that produced it.

We addressed this with explicit operations, one authoritative state-update path, versioned contracts, and feedback containing only questions actually asked and products actually shown.

### 2. Recall and precision pulled in opposite directions

Strict early filtering made results look cleaner but sometimes removed the purchased item before ranking. A broad fixed pool protected Hit Rate but admitted category false positives. Our public ablations showed that the recall-compatible Top-50 boundary was the safer submitted choice, while final ranking and clarification handled precision later in the pipeline.

### 3. Fewer questions did not always mean a better agent

Reducing clarification from two turns to one improved conversational efficiency but lowered ranking quality. We therefore treated clarification as a measurable policy tradeoff rather than assuming that the shortest conversation is always best.

### 4. Reproducibility had to cover more than code

A reproducible agent needs the same entry point, data checksum, runtime configuration, evaluator, and published claims. We separated source from the organizer's data, verified the frozen kit by SHA-256, blocked network access during the offline evaluation check, and kept private labels and synthetic proxy data outside the reported Public-200 result.

## What we learned

We learned that conversational search depends as much on **state semantics and orchestration** as on retrieval scores. A strong component can still reduce end-to-end quality when its assumptions do not match the next component. Small interface decisions—who owns state mutation, how overrides are represented, and whether a question was actually shown—can change both relevance and Mean Turns to Conversion.

We also learned to separate development evidence from generalization claims. On the public set, we can compute

$$
\mathrm{Hit@10}=\frac{1}{N}\sum_{i=1}^{N}\mathbf{1}[r_i\leq 10]
$$

and

$$
\mathrm{MRR}=\frac{1}{N}\sum_{i=1}^{N}\frac{1}{r_i},
$$

but improvements on 200 development sessions do not guarantee the same improvement on the organizer's 800 private sessions. This changed how we documented ablations and limitations.

## Accomplishments

Using the **unchanged official evaluator** on all **200 public development sessions**, the submitted CPU integration achieved:

| Metric | Result |
|---|---:|
| Hit Rate@10 | **98.5% (197/200)** |
| MRR | **0.888375** |
| Mean Turns to Conversion | **3.205** |
| Efficiency | **0.779500** |
| Evaluator TechnicalScore | **0.914913** |

The run completed without recorded pipeline exceptions. Compared with the earlier component ranking baseline, the integration preserved Hit Rate@10 and MTTC while increasing MRR from `0.884625` to `0.888375` and the evaluator composite from `0.913788` to `0.914913`.

These are **public development-set results**, not private-test results and not the overall event judging score.

## What's next

Our current submission is deliberately lightweight and reproducible, but it remains primarily lexical and rule-based. Complex negation, ambiguous variants, missing prices, and broad category names can still produce false positives. The profile is a weak input prior rather than learned cross-session memory.

Given more time, we would improve leaf-category normalization, attribute and negation extraction, clarification-question selection, and override handling. We would also evaluate dense semantic retrieval and local LLM reranking as separate, controlled extensions with a reliable CPU fallback instead of claiming them based only on unused research code.

## Built with

1. Python
2. SQLite
3. SQLite FTS5
4. BM25
5. Reciprocal Rank Fusion
6. Natural Language Processing
7. Information Retrieval
8. Recommender Systems
9. Conversational AI
10. Rule-based NLP
11. JSON
12. Git
13. GitHub
14. Visual Studio Code
15. Python unittest
16. Python bdb
17. macOS
18. Amazon Reviews 2023
19. TikTok TechJam Participant Kit
20. OpenAI Codex
