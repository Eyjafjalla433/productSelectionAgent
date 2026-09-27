# Claude Sonnet 4.5 vs DeepSeek Flash — English shopping workflow evaluation

Date: 2026-09-28 (Asia/Shanghai). Real API calls; synthetic shopper messages and catalogs only.

This is the pre-integration evaluation snapshot. The subsequent fixes and their validation are documented in the [integration verification report](../claude_integration_verification/REPORT.md).

## Decision

Claude can provide comparable English requirement parsing and short, grounded responses **after adapting the integration**. The current production gateway route is not an equivalent replacement for the DeepSeek route: it uses the supplementary `RequirementEnhancer` and does not enable `ResponseWriter`.

The evaluation changes no production provider priority, prompts, or runtime wiring. Experimental gateway token limits and aligned runtime wiring exist only inside evaluation processes.

## Scope and method

- 16 English requirement cases, repeated twice per provider: explicit requirements, preferences, exclusions, clearing a dimension, restoring an excluded material, references to prior state, color revisions, hard-to-soft changes, numeric budgets, positive/negative audience requests, category switching, non-edit questions/courtesy, compound edits, and instruction injection.
- 9 English response cases, repeated twice: grounded cards, material reversal, required clarification, failed saving, no results, selection versus purchase, catalog injection, retaining preferences, and unknown availability.
- Two six-turn conversations per provider: the current runtime configuration and a test-only aligned configuration using `PrimaryRequirementParser` plus `ResponseWriter`. The conversation checks state changes, candidate membership, Undo, material clearing, soft linen preference, and the preference summary. Audit chains are verified.
- Separate 5-card and 10-card response tests, each repeated twice per provider.
- Chinese and mixed-language cases executed before the user's English-only clarification remain in the original evidence files but are excluded from the English score and recommendations.

The parser score compares the resulting intended state, with aliases normalized, rather than requiring identical JSON order or identical edit sequences. Direct parser tests use the same prompts and no rule hints; the runtime test exercises real rule hints and state machinery. The direct state checker is an evaluation helper, not a replacement for the application's state reducer.

Response checks include the existing schema/card/evidence validator, required-question preservation, English output in both replies and cards, selected forbidden claims, and case-specific operation/no-result checks. These are contract checks, **not a complete semantic correctness score**. Raw outputs are retained for manual review.

## Current production behavior and the gateway issue

The original gateway path successfully completed the six-turn basic conversation with correct states and products. However, every model enhancement added no accepted updates and `ResponseWriter` was disabled. Rule-based success is not proof that Claude's full parser or generated responses are working.

When the full parser was called through the unmodified gateway adapter, the first eight recorded cases failed (0/8). Repeating the same failure across the remaining cases was stopped, and skipped cases are not counted as failures or passes. Two direct response-writer probes at the normal 2400-token request also fell back.

A 60-second diagnostic captured a response with multiple concatenated JSON code blocks, including contradictory empty updates. Extending the timeout therefore does not solve the protocol problem. The adapter correctly rejects this response; extracting the first object would hide conflicting output.

Controlled single-case diagnostics found:

| Request setting | Observed result |
|---|---|
| Original wrapping, 512 output limit | One valid JSON object |
| Original wrapping, 1400 output limit | Concatenated JSON; rejected |
| User-only instructions, 1400 limit | Rejected |
| System-only instructions, 1400 limit | Rejected |
| `format=json`, 1400 limit | Rejected |
| Temperature 0.3, 1400 limit | Rejected |

This establishes a request-setting-dependent failure through this gateway. It does not identify whether the root cause lies in gateway orchestration, proxy behavior, or model generation, and it is not a general claim about the native Anthropic API.

## Candidate configuration

The test-only candidate caps gateway `options.num_predict` at 1024 while retaining the original task prompts and strict JSON validation. For the aligned runtime only, it explicitly attaches the full requirement parser and response writer. DeepSeek retains its existing 1400-token parser and 2400-token writer limits; this is a comparison of usable integrations, not equal-budget model benchmarking.

An earlier 512-token candidate passed small-output cases but failed the 10-card stress request. Both 768 and 1024 succeeded on a diagnostic 10-card request. The final repeat suite uses 1024, including repeated 5/10-card tests.

| English-only measure | Claude candidate (1024 cap) | DeepSeek existing configuration |
|---|---:|---:|
| Requirement edits | 32/32 | 32/32 |
| Response contract + English checks | 18/18 | 15/18 |
| Six-turn state and product checks | 6/6 | 6/6 |
| 5-card output, two runs | 2/2 | 2/2 |
| 10-card output, two runs | 2/2 | 2/2 |
| Median parser latency | 2.53 s | 1.05 s |
| Median writer latency | 3.06 s | 1.55 s |
| Median aligned runtime turn | 5.07 s | 2.93 s |

Detailed scores and latency are in [SUMMARY.json](SUMMARY.json).

## Qualitative observations

- Claude's candidate outputs generally retain authoritative operation status, required questions, product identities, and evidence IDs. They are often concise and sometimes copy the authoritative response nearly verbatim; acceptance is not proof of more natural writing.
- In the initial DeepSeek response suite, two English cases produced Chinese replies/cards and one no-results response introduced an unsolicited question that caused fallback. The repeated cases subsequently passed. The English-only requirement means language drift is a failure even when the facts are correct.
- Manual review of the earlier 512-token candidate found a broad phrase, “more blue linen options,” despite one product having unknown material; its product card correctly preserved the uncertainty. Automated contract acceptance does not catch every overgeneralization.
- One DeepSeek availability answer strengthened “listed variant XL; other sizes unknown” into “other sizes aren't listed.” Unknown catalog coverage must not be presented as verified absence.
- No purchase was executed. These checks exercise session-local selection behavior and synthetic catalogs, not stock verification or real ordering.

## Changes needed before equivalent production use

1. Enable the full requirement parser and response writer for the gateway explicitly. Choosing a reachable endpoint alone does not make these paths equivalent.
2. Apply and regression-test a gateway-specific output limit; 1024 is a tested candidate for this fixture set, not a universal safe maximum. Preserve whole-response JSON validation and explicit fallback.
3. Share gateway operation budgets and call counters across parser/writer calls; do not clone the gateway in the same way as a stateless provider.
4. Replace the response writer's hard-coded `fit_reason_source='deepseek'` with the actual provider name before enabling Claude-generated cards.
5. Make English mandatory in generation and validate the output language. The existing prompt says to follow the user's language; it does not strictly enforce English.
6. Upgrade startup readiness checks to cover the actual JSON contract. A successful `{"ok":true}` check does not establish full-parser or multi-card compatibility.

## Evidence and reproduction

The summary is versioned with this report. Raw run files below remain in ignored `output/`; the paths identify local generated artifacts, not files included in a fresh checkout.

- DeepSeek original paired run (`output/model_comparison_20260928/deepseek.json`, local generated artifact)
- Original gateway failure sample (`output/model_comparison_20260928/gateway_default_partial.json`, local generated artifact)
- Original gateway writer/runtime probes (`output/model_comparison_20260928/gateway_native_diagnostic.json`, local generated artifact)
- Captured concatenated response (`output/model_comparison_20260928/gateway_envelope_diagnostic.json`, local generated artifact)
- Final 1024 candidate paired run (`output/model_comparison_20260928/candidate_1024/gateway.json`, local generated artifact)
- Final gateway 5/10-card tests (`output/model_comparison_20260928/candidate_1024/stress_gateway.json`, local generated artifact)
- Final DeepSeek 5/10-card tests (`output/model_comparison_20260928/candidate_1024/stress_deepseek.json`, local generated artifact)

From the repository root (these commands consume real API quota):

```powershell
python -B scripts/compare_cloud_models.py --modes deepseek --output output/recheck_deepseek
python -B scripts/compare_cloud_models.py --modes gateway --gateway-token-cap 1024 --output output/recheck_gateway
python -B scripts/stress_cloud_writer.py --gateway-token-cap 1024 --output output/recheck_cards
python -B scripts/diagnose_gateway_json.py
```

Two repetitions and one synthetic six-turn scenario are a practical regression sample, not statistical proof of equal model quality. Latencies include provider/gateway/network overhead; runs were not randomized or performed at identical times. Exact billing is not inferred from token counts, and failed requests may still consume quota. No API keys, authorization headers, or shopper personal data are included in the evidence.
