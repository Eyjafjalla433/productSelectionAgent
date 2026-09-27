# Claude integration verification

Verified on 2026-09-28 using real gateway calls with synthetic English inputs. This run uses the application adapter directly, without an experimental output-cap override.

## Completed changes

1. Both `AgentRuntime` and the optional `GatewayRuntime` entry point enable `PrimaryRequirementParser` and `ResponseWriter` for `aws_bedrock_gateway`. Parser, response writer, and comparison adapter share the same gateway object. The existing DeepSeek writer timeout and local/off behavior remain intact.
2. Every gateway request uses `min(requested_max_tokens, 1024)` for `options.num_predict`. Smaller probes retain their requested budget. Concatenated objects, incomplete JSON, truncated output, and tool calls remain rejected; the adapter never silently selects the first object.
3. Generated card `fit_reason_source` and applied `response_assist.provider` come from the actual completion provider. Failed generation removes stale generated reasons/source labels. Generation instructions request English for replies and card explanations.

## Verification

| Check | Result |
|---|---|
| English parser cases, actual gateway adapter | 16/16 |
| English response cases, actual gateway adapter | 9/9 |
| Six-turn current application runtime | State, candidate sets, Undo, soft preferences, generated responses, and audit chain pass |
| Current runtime source attribution | Every generated card and response identifies `aws_bedrock_gateway` |
| Current runtime parser/writer provider identity | Same object |
| Explicitly aligned runtime cross-check | Six turns pass as well |
| 5-card and 10-card generation | Both pass for gateway and DeepSeek, with no test cap override |
| Workflow unit/regression suite | 544 pass |
| Shopping-agent suite | 47 pass |
| Server suite | 33 pass |
| Gateway suite | 15 pass |

The gateway suite covers both runtime entry points, cumulative usage, shared call limits, expiry between parser and writer, small/large requested output budgets, strict rejection of concatenated JSON, and error fallback. Source tests cover both providers and removal of stale attribution after failure.

The summary below is versioned with this report. Raw run files remain in ignored `output/` and are not included in a fresh checkout.

Evidence: [machine-readable summary](SUMMARY.json), gateway paired run (`output/claude_integration_verification/gateway.json`, local generated artifact), gateway card stress tests (`output/claude_integration_verification/stress_gateway.json`, local generated artifact), DeepSeek card stress tests (`output/claude_integration_verification/stress_deepseek.json`, local generated artifact).

Reproduce the real checks from the repository root; these commands consume quota:

```powershell
python -B scripts/compare_cloud_models.py --modes gateway --repeats 1 --output output/claude_integration_verification
python -B scripts/stress_cloud_writer.py --repeats 1 --output output/claude_integration_verification
```

The 1024 cap is a tested mitigation for the observed team-gateway behavior, not a guarantee for arbitrary output size or future upstream behavior. Invalid or over-budget generation still falls back explicitly. This verification does not claim exhaustive description/comparison quality or AWS deployment validation. Restart an already-running backend to load the changes; no long-running application server was restarted by these tests.
