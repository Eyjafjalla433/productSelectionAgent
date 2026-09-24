# Shopping Copilot: Stateful Conversational Product Search

**Tagline:** A shopping agent that remembers changing requirements, asks when context is missing, and searches 50,000 products with an offline CPU pipeline.

## Inspiration

Shopping is rarely a single, perfectly specified search. A shopper may start
with a black dress under $50, change the color, remove the budget, or switch to
shoes entirely. An assistant must know which requirements still apply and which
should be forgotten. Repeating generic questions or carrying stale constraints
forward makes that experience frustrating.

We built Shopping Copilot to make these changes explicit and observable,
connecting conversation understanding, state and the decision to ask or recommend.

## What it does

Our agent searches the organizer's frozen catalog of 50,000 products. It
accumulates requirements, separates hard conditions from soft preferences,
handles exclusions and clears stale product-specific conditions when the
shopper changes direction.

It can ask before retrieval when the category is missing, or after retrieval
when the pool is too broad. Otherwise it returns up to ten ranked catalog IDs.
Logs show state changes, policy decisions, candidate counts and actual outputs.
The agent enforces the ten-turn limit and records only questions actually asked.

## How we built it

We connected five responsibilities through versioned interfaces: intent/query
understanding, retrieval, state/context, ranking/clarification, and evaluation/
integration. The submitted sequence is:

`User → Intent → State → Pre-policy → Retrieval → Ranking/Post-policy → Response → State feedback`

Intent produces explicit set, clear, exclude and remove-exclusion operations.
One state consumer applies them. Retrieval uses accumulated state rather than
treating the latest short reply as a standalone search. Session, turn and state
versions make stale or cross-session outputs detectable.

The submitted CPU configuration combines SQLite FTS5/BM25 keyword/category
routes, catalog-evidence retrieval and locked CPU reciprocal-rank features.
Policy 4A performs two evidence-collection turns, choosing question content from
the current State. From turn three onward, retrieval quality and feedback drive
dynamic 4B recommend/clarify decisions. Module 2 supplies a deterministic,
recall-compatible Top-50 pool to protect coverage. State-evidence and stricter
variable-pool research profiles remain available as ablations.

We used Python, SQLite, Git/GitHub, VS Code, unittest and Python's bdb debugger,
with Codex assistance for integration, debugging and documentation. The runtime
has no third-party Python dependencies and needs no GPU, model API or credentials.

## Challenges we ran into

Integration failures were often about meaning, not syntax. Two parsers could
disagree about the same sentence; an exclusion could become a positive slot;
fixed-size candidate contracts could encourage unsuitable padding. Catalog
store names could even be ordinary words, causing accidental brand constraints.

We addressed these with explicit operations, a single state-update path,
separate exclusions and conservative brand cues. For the submitted score profile,
we retained module 2's fixed Top-50 boundary because the public ablation showed
that stricter early filtering removed valid targets.

Another challenge was balancing fewer questions against higher target ranking.
We report this tradeoff instead of assuming faster conversation is always better.

## Accomplishments

Using the unchanged official evaluator on all 200 public development sessions,
the complete CPU integration achieved:

- **Hit Rate@10: 98.5% (197/200)**
- **MRR: 0.888375**
- **Mean Turns to Conversion: 3.205**
- **Efficiency: 0.779500**
- **Evaluator TechnicalScore: 0.914913**

The run completed without recorded pipeline exceptions. We built multi-turn
demos and versioned traces for missing context, condition changes, category
switches and post-retrieval clarification.

These are public development-set results, not private-test results. The earlier
component-4 fixed-two-question baseline reached MRR 0.884625 and MTTC 3.205.
Our full integration raises MRR to 0.888375 at the same MTTC and improves the
evaluator composite from 0.913788 to 0.914913. TechnicalScore
is not the overall event score, and this small public-set gain does not establish
private-set generalization.

## What we learned

A module that works alone may fail when its output means something different
to the next module. Clear contracts and execution feedback helped locate those
failures. Reproducibility also requires the submitted entry, dependencies, data
hashes, evaluation command and published claims to describe the same configuration.

## What's next

We plan controlled clarification/ranking ablations, improved attribute and
negation extraction, and separately validated Dense/LLM semantic paths. We would
also improve question selection and explore safe profile updates without
confusing historical preferences with current hard requirements.

The current entry is primarily rule-based. It does not claim Dense retrieval,
Qwen inference, learned persistent profiles, verified variant availability or
real purchasing functionality.
Category matching can also produce false positives, such as dress shirts for a
dress request or unrelated products from a broad catalog taxonomy. Our state
demonstration should not be mistaken for perfect product relevance.

## Data, models and cost

We use the organizer's frozen participant kit derived from **Amazon Reviews
2023**, published by **UCSD McAuley Lab**, limited to the provided
Clothing_Shoes_and_Jewelry catalog. Private evaluation sessions and hidden intent
cards are not Agent inputs. Synthetic proxy data is not used for these results.

No hosted API or local LLM is invoked. Model tokens and API costs are zero;
hardware/electricity costs are not estimated. The technical report includes
measured startup, response latency and memory. Data attribution and third-party
terms are retained.

## Built with

Python; SQLite FTS5; BM25; reciprocal rank fusion; rule-based NLP; Git; GitHub;
Visual Studio Code; unittest; bdb; Codex-assisted development.

## Team contributions

Our five responsibilities covered intent/query understanding, retrieval,
state/context, ranking/clarification, and evaluation/integration. The repository
contribution table distinguishes component work from integration changes.
Team identities are supplied in the Devpost roster.
