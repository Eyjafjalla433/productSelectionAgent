# Ranking Pipeline

This directory implements component 4 and D:

- semantic reranking of the `CandidateSet` returned by retrieval
- hard-constraint filtering and a locked weighted-RRF pre-rank
- `Qwen3-Reranker-0.6B` local pointwise scoring on the Top20 candidate subset
- locked weighted-RRF + pointwise-score fusion, with deterministic fallback
- structured per-candidate scores/reasons and dynamic locked/hybrid routing
- over-general candidate-pool detection and attribute-aware clarification policy
- reproducible public-set training and full-pipeline evaluation

## Layout

```text
ranking_pipeline/
  context.py            profile compaction and intent-override parsing
  agent.py              ranking-pipeline agent adapter
  override_aware_agent.py per-turn override-aware requirements collector
  memory_context.py     intent-router/state-memory adapters
  prompt.py             compact listwise prompt and strict JSON parser
  qwen_reranker.py      Qwen3-Reranker-0.6B pointwise adapter
  contextual_ranking.py hard filter + pre-rank + final reranker + policy
  training_data.py      public/synthetic pair construction
  distribution_alignment.py aligned public+3021 training data
  train_reranker.py     scoring-head training script
  evaluate_agent.py     official evaluator wrapper
  diagnose_synthetic.py synthetic 3,021 diagnostic summarizer
  summarize_results.py  saved-result comparison table
  checkpoints/          trained scoring head
  results/              local evaluator results
```

`ranking_pipeline` is a sibling of `retrieval-and-reranking`. Import path
bootstrapping is kept in `retrieval-and-reranking/techjam_agent/__init__.py`
and this package's `__init__.py`, so both the official evaluator and this
package can be launched from their natural working directories without setting
`PYTHONPATH`.

`checkpoints/` is local-only and ignored by `ranking_pipeline/.gitignore`.
Do not commit model weights; install or re-train them on each machine.

## Model Decision

Use `Qwen3-Reranker-0.6B`, not `Qwen3-VL-Reranker-2B`.

The catalog and simulated dialogue are text only. The VL reranker adds a vision
backbone that is unused here, so it only increases memory and latency. The
0.6B text model is small enough to run offline on a 6GB consumer GPU and is
also usable as a CPU fallback with reduced throughput.

The default checkpoint is `tomaarsen/Qwen3-Reranker-0.6B-seq-cls`, a
sequence-classification conversion of `Qwen/Qwen3-Reranker-0.6B`. This lets the
same `AutoModelForSequenceClassification` API be used for zero-shot inference
and scoring-head training.

## Top20 vs Top50

Feed the locked pre-ranker's Top20, not the original Top50, and not the first
20 rows of the raw Top50 list.

On the public precomputed files:

```json
{
  "sample_count": 200,
  "top50_coverage": "199/200",
  "raw_top50_first20_coverage": "192/200",
  "top10_hit": "198/200",
  "top20_coverage_lower_bound_from_locked_top10": "198/200",
  "mrr_at_10": 0.921843
}
```

The raw first-20 slice loses targets compared with the locked Top10. The locked
Top10 covers 198/200, so the same locked ordering truncated to Top20 is the
correct final-model input. Using all 50 only increases prompt length, latency,
and small-model ranking noise.

## Install

From the repository root:

```powershell
python -m pip install torch transformers peft bitsandbytes accelerate
python -m unittest discover -s ranking_pipeline/tests -t . -v
```

The competition catalog is not committed. Download and verify it from the
official `participant-kit` GitHub Release into
`retrieval-and-reranking/data/catalog.jsonl`.

## Train The Scoring Head

The official scope permits local scoring logic and prompt tuning, while
full-parameter training of base foundation models is out of scope. Training now
uses PEFT LoRA: the base transformer stays frozen, LoRA trains the attention
and MLP projections, and `score` is saved as a `modules_to_save` head. Only
`adapter_model.safetensors`, `adapter_config.json`, and tokenizer files are
written; the frozen backbone is not re-saved.

The default `aligned` strategy combines the public 200 gold set with the
synthetic 3,021 proxy set. The synthetic proxy is public-schema compatible but
not the organizer private-800 set: use it only for distribution alignment and
calibration, never as the final reported score. Synthetic positives use target
products as positives, while negatives are sampled from the same leaf category
with weights derived from `quality_tier` and `selection_frequency`.

For the joint strategy, public positives default to `5.0` loss weight (the
recommended 5-10x band), and each epoch is evaluated on the public set. The
best public-accuracy checkpoint is restored before saving. A public-only run
defaults to one epoch; an aligned run defaults to two epochs.

From a fresh PowerShell terminal at the repository root, continue training from
the existing LoRA checkpoint:

```powershell
$env:PYTORCH_CUDA_ALLOC_CONF='expandable_segments:False'
python -m ranking_pipeline.train_reranker `
  --data-strategy aligned `
  --epochs 2 `
  --batch-size 4 `
  --adapter-checkpoint ranking_pipeline\checkpoints\qwen3-reranker-0.6B-shopping-lora `
  --output ranking_pipeline\checkpoints\0.6Blora_aligned_from_shopping_lora `
  --log-interval 50
```

`PYTORCH_CUDA_ALLOC_CONF` works around the CUDA allocator error
`Unrecognized CachingAllocator option: expandable_segments=True`. If you are
starting from scratch instead of continuing from LoRA, omit
`--adapter-checkpoint`.

Epoch and best checkpoints are written as siblings of `--output`, not inside
that directory: `<output>_epochN` and `<output>_best_pubaccX.XXX`.

```powershell
python -m ranking_pipeline.train_reranker `
  --data-strategy aligned `
  --epochs 2 `
  --batch-size 4 `
  --negatives-per-positive 4 `
  --synthetic-negatives-per-positive 8 `
  --synthetic-hard-negatives-per-positive 4 `
  --synthetic-retrieval-mode lite `
  --max-length 512 `
  --log-interval 50
```

The base model loads with 4-bit quantization by default. Use
`--no-load-in-4bit` for full precision, and use `--log-interval 50` (default)
for step-level progress. Synthetic negatives first take hard examples from the
retrieval Top-K and then fill the remaining slots from the same leaf category.

Listwise mode switches the objective from pointwise BCE to a per-query
softmax contrastive loss. It defaults to public Top-20 candidates; increase it
with `--public-top-k 50` when GPU memory allows.

```powershell
python -m ranking_pipeline.train_reranker `
  --loss listwise `
  --data-strategy public `
  --epochs 1 `
  --batch-size 1 `
  --public-top-k 20 `
  --public-retrieval-mode lite `
  --max-length 512 `
  --log-interval 10
```

`Qwen3Reranker.load` detects a local `adapter_config.json`, loads the base model
from the recorded `base_model_name_or_path`, then attaches the adapter with
`PeftModel.from_pretrained`.

Set `--data-strategy public` for the previous public-200-only run, or
`--data-strategy synthetic` for a synthetic-only dry run/ablation. The old
`techjam-precomputed-rankings-200-and-3021` directory is no longer required:
pass `--public-top50` only when a precomputed public Top50 file is available;
otherwise the builder samples negatives from the synthetic product CSV/catalog.

First run `--dry-run` to inspect the generated pairs without downloading model
weights.

After generating synthetic ranked predictions, emit diagnostics only:

```powershell
python -m ranking_pipeline.diagnose_synthetic `
  --rankings ranking_pipeline\results\synthetic-3021-ranked.jsonl `
  --top-k 10
```

The diagnostic report contains per-tier recall, score distribution, and
over-general rate. Do not use it as the official evaluation metric.

The precomputed Top50 diagnostic output is saved to
`ranking_pipeline/results/synthetic-3021-diagnostics.json` and records
`per-tier recall` from `0.950` to `0.986`, a `43.36%` over-general rate, and
the fallback score distribution across 30,210 scored candidates.

## Run The Full Pipeline

The wrapper invokes the unchanged official evaluator from
`retrieval-and-reranking`.

```powershell
# locked baseline
python -m ranking_pipeline.evaluate_agent --mode locked --retrieval-mode lite

# hard filter + locked pre-rank, no local model
python -m ranking_pipeline.evaluate_agent --mode hybrid --retrieval-mode lite

# full pipeline with the trained Qwen3-Reranker-0.6B scoring head
python -m ranking_pipeline.evaluate_agent --mode local --retrieval-mode lite

# optional: consume conversation-state-memory + intent-recognition contracts
python -m ranking_pipeline.evaluate_agent --mode local --retrieval-mode lite `
  --use-state-memory --use-intent-router
```

## Output Contract

`RankingAgent.respond` returns an evaluator-compatible response, not a raw
candidate set:

```json
{
  "message": "Here are the best matches for all requirements you shared.",
  "ask_attribute": null,
  "recommendations": [
    {"parent_asin": "P103"},
    {"parent_asin": "P028"},
    {"parent_asin": "P901"}
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0}
}
```

`recommendations` is already ordered. The evaluator preserves that order and
takes up to the first 10 valid IDs. For `locked-exact`, the order comes from
`LockedWeightedRrfTop10Reranker`'s weighted-RRF score, descending. Internally
the reranker also keeps `rank`, `score`, and `evidence`, but the agent boundary
only exposes the evaluator's required `parent_asin` list.

## Metrics

- Hit@10: fraction of sessions where the ground-truth `parent_asin` appears in
  the first 10 recommendations.
- MRR: mean of `1 / best_rank`; zero when the target is never returned.
- MTTC: mean first-hit turn. A miss counts as `MAX_TURNS + 1` (`11`), so lower
  is better.
- Efficiency: `clamp((11 - MTTC) / 10, 0, 1)`.
- Technical: `0.50 * Hit@10 + 0.30 * MRR + 0.20 * Efficiency`.

## Intent Override Handling

`ranking_pipeline/agent.py` now defaults to
`OverrideAwareRequirementsCollector`, which is tested in
`ranking_pipeline/tests/test_override_aware_agent.py`. Override resolution is
slot-aware and conservative:

- a same-value replacement promotes the old soft preference to hard,
- a same-slot replacement removes only the conflicting old slot,
- an explicit negation removes the negated preference,
- a generic `ignore my earlier preference` falls back to the last explicitly
  disclosed soft preference.

On the public 200, this change raises the locked-exact `Technical` score from
`0.899664` to `0.913788`, an increase of `0.014124`. The `intent_override`
subset improves from Hit@10 `0.866667` / MRR `0.655595` / MTTC `4.633333` to
Hit@10 `0.966667` / MRR `0.749444` / MTTC `3.833333`.

## Clarification Policy Behavior

`RecommendationClarificationPolicy` selects `recommend` or `clarify` from:

- the model's `need_clarification` flag,
- hard-constraint `constraint_conflicts`,
- the candidate pool's `is_over_general` signal,
- Top1/Top2 confidence and score margin,
- attributes already asked, to avoid repeated questions.

A clarification is returned as `ask_attribute` plus `message`, matching the
official evaluator's dialogue contract. Dynamic rerank selection can fall back
to locked when the turn is late, hard-hit rate is low, or the candidate pool is
large.

For component 5 integration, submit `--mode locked --retrieval-mode exact`
with policy disabled. Keep the policy as an offline/ablation path because on
the public 200 it raises MTTC to `9.955` and lowers Technical.

## Context Source

The official ``reset(session_id, user_profile)`` profile is the primary
long-term user signal. ``conversation-state-memory`` supplies the short-term
``ContextSnapshot``, and ``intent-recognition`` supplies the current-turn
``IntentResult``. ``ranking_pipeline.memory_context`` merges official profile
tags with snapshot ``profile_hints``; current-turn hard constraints still
override any profile hint.

The main ranking agent uses
``OverrideAwareRequirementsCollector``, a subclass of the official requirements
collector. The official collector therefore remains the retrieval/ranking
requirement contract; the subclass adds intent-override-aware slot replacement
without changing the boundary the evaluator validates. The
optional ``IntentResult`` and ``ContextSnapshot`` are supplemental inputs:
they provide current-turn route/ambiguity signals, accumulated slots,
exclusions, session summary, and profile hints. They are passed into
``set_session_context`` rather than replacing the collector output. The
long-term official profile is still the safest personalization signal; do not
let the state-memory in-memory ``UserProfile`` replace it.

The final LLM/listwise prompt is capped to ``min(top_n, max(top_k * 2, 10))``
candidates after the locked pre-rank. Prompt chars and an approximate token
count are stored on the reranker as ``last_prompt_chars`` and
``last_prompt_tokens`` for latency/telemetry verification.

Formal `exact` dense public-200 results:

| mode | Hit@10 | MRR | MTTC | Technical |
|---|---:|---:|---:|---:|
| `locked-exact-override-main.json` | 0.985 | 0.884625 | 3.205 | 0.913788 |
| `locked-exact.json` | 0.970 | 0.870548 | 3.325 | 0.899664 |
| `local-exact.json` | 0.975 | 0.786365 | 3.32 | 0.877009 |
| `hybrid-exact.json` | 0.835 | 0.480437 | 4.39 | 0.693831 |
| `local-exact-policy.json` | 0.850 | 0.503417 | 9.955 | 0.596925 |

`locked-exact-override-main.json` is the current main-path result: the
override-aware collector raises `Technical` by `0.014124`, from `0.899664` to
`0.913788`. `local-exact` still lowers MRR versus the locked path, so the
locked exact path with the override-aware collector remains the recommended
submission configuration.

When CUDA reports `Unrecognized CachingAllocator option`, run exact/local
experiments with:

```powershell
$env:PYTORCH_CUDA_ALLOC_CONF='expandable_segments:False'
$env:TECHJAM_RERANKER_DEVICE='cuda'
```

The policy layer selects the next `ask_attribute` instead of always returning
`other`. It accounts for prior asked attributes, missing requirement attributes,
model confidence, and the candidate pool's over-general signal. The agent keeps
this short-term clarification state and forwards it to the reranker through the
same `set_session_context` boundary used for the long-term profile.

## Unit Tests

From the repository root:

```powershell
python -m unittest discover -s ranking_pipeline/tests -t . -v
```

The ranking-pipeline suite currently contains 34 tests and does not download
model weights. The original retrieval-and-reranking suite can still be run
unchanged from that directory with:

```powershell
cd retrieval-and-reranking
python -m unittest discover -s tests -v
```

`ranking_pipeline` does not modify tracked files under
`retrieval-and-reranking`; the only integration adapter is
`ranking_pipeline/agent.py`.
