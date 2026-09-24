# Clarification policy ablation

All public results use the unchanged official evaluator, frozen 50,000-product
catalog and 200 released development sessions. The 1,377-session result uses the
non-low-likelihood subset of the local synthetic proxy and is not private-800
evidence.

## Policies

- `state_evidence` (submitted default before this experiment): ask while the
  accumulated State evidence is below four, with at most two questions.
- `fixed_two_dynamic`: collect two clarification answers before retrieval; after
  retrieval, 4B may ask one additional question on empty/negative-quality paths.
- `one_then_value`: ask at least once and ask a second time only while State
  evidence is below four; 4B has the same dynamic allowance.

All three retain `Intent -> State -> 4A -> module-2 recall-compatible Top-50 ->
locked 4B -> State feedback`. No ground-truth ID, sample ID or evaluator label is
an Agent input.

## Official public 200

| Policy | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---:|---:|---:|---:|---:|
| Component-4 published baseline | 0.985 | 0.884625 | 3.205 | 0.7795 | 0.913788 |
| `state_evidence` | 0.985 | 0.879208 | **3.095** | **0.7905** | 0.914362 |
| **`fixed_two_dynamic`** | **0.985** | **0.888375** | 3.205 | 0.7795 | **0.914913** |
| `one_then_value` | 0.980 | 0.875708 | 3.235 | 0.7765 | 0.908012 |

`fixed_two_dynamic` is the best public composite and MRR. `state_evidence` is
faster. `one_then_value` loses public Intent Override coverage (0.90 Hit@10) and
is not competitive.

## Non-low-likelihood synthetic proxy 1,377

| Policy | Hits | Hit@10 | MRR | MTTC | Efficiency | TechnicalScore |
|---|---:|---:|---:|---:|---:|---:|
| `state_evidence` | 1,359 | 0.986928 | 0.906284 | **3.101670** | **0.789833** | 0.923316 |
| **`fixed_two_dynamic`** | **1,366** | **0.992012** | **0.914862** | 3.143065 | 0.785694 | **0.927603** |
| `one_then_value` | 1,351 | 0.981118 | 0.909847 | 3.274510 | 0.772549 | 0.918023 |

The fixed warm-up gains seven hits and 0.008578 MRR versus `state_evidence`, at
a cost of 0.041395 mean turns. Its largest coverage gain is Intent Override:
0.995192 versus 0.971154 Hit@10. This supports a fixed warm-up as the best tested
scoring profile, but it should be described accurately as fixed pre-retrieval
evidence collection followed by dynamic post-retrieval handling, not as a fully
dynamic question-count policy.

## Decision

For automated score, `fixed_two_dynamic` is best on both evaluated sets. For
minimum interaction turns, `state_evidence` remains best. Do not promote a new
default until the team chooses between those objectives and reviews the product
experience tradeoff. `one_then_value` should not be promoted.

Machine-readable reports:

- `results/ablation-fixed-two-dynamic-public200.json`
- `results/ablation-one-then-value-public200.json`
- `results/ablation-fixed_two_dynamic-high1377.json`
- `results/ablation-one_then_value-high1377.json`
- `results/integrated-synthetic3021-tier-metrics.json`
