# Optional AWS Bedrock gateway integration

This is a removable integration package. **`python -m agentic_workflow` now defaults to automatic cloud selection.**
At startup it probes the gateway and DeepSeek concurrently with one small billable JSON request each.
If both work, the gateway wins; otherwise the healthy provider is selected. If both fail, startup continues
in explicitly reported model-free mode. Selection lasts for the process; runtime failures do not switch providers.
Use `--model-provider off` for offline startup, or `gateway`, `deepseek`, or `local` to select explicitly.
The core lazily imports this package for gateway selection; if removed, auto mode can still choose DeepSeek.
This package does not read, import, or depend on `ShowMeYourAgent-Starter-Kit/`; that reference repository can be removed separately.
This package depends on the current project and is not a standalone product search system.

## Integration scope and verification status

The organizer's AWS Resource Usage Model slides explicitly permit Lightsail and Bedrock
Claude Sonnet 4.5 JSON API calls. Refer to the organizer's email and Slack for team quotas,
the actual URL, model alias, and API key. This package does not provision AWS resources,
change quotas, or use credentials from repository examples.

Protocol reference: the [official starter kit](https://github.com/kenken64/ShowMeYourAgent-Starter-Kit),
with local reference revision `bffda0d15c494abef9202cab8feb13e067a40d1d`. The relevant example is
`weather_demo.py`: non-streaming Ollama `POST /api/chat`, `X-API-Key`, and `options.num_predict`.
Responses are read from `message.content`, `prompt_eval_count`, and `eval_count`. Native gateway
tool calling is not required, and Claude XML tool calls are not parsed. Responses containing tool
calls are rejected; tool results claimed by the model are not executed.

The adapter implements the existing `complete_json` interface. Both gateway and DeepSeek routes now
use `PrimaryRequirementParser` for complete requirement edits and `ResponseWriter` for English
conversation text and grounded product cards. The Description/Comparison adapter remains connected.
All gateway stages share the same provider instance, call counter, and operation deadline.
Existing Python validation and policies still control retrieval, state changes, and final confirmation.
**This does not replace the entire agent with an LLM.** Product-card `fit_reason_source` and
`receipt.response_assist.provider` report the actual completion provider (`aws_bedrock_gateway` or
`deepseek`); failed generation removes stale generated card reasons and uses the existing fallback.
If the provided endpoint uses an API Gateway/Lambda-specific JSON format instead of an Ollama-compatible
interface, this package cannot be used directly. Adapt it to the actual request and response format
provided in Slack first.

Local tests with simulated responses and integration tests using the actual project runtime are complete.
A real startup probe succeeded against both the team gateway and DeepSeek on 2026-09-28.
The full gateway parser/writer integration was subsequently verified with 16 English parsing cases,
9 response cases, a six-turn conversation, and 5/10-card generation, without test-only adapter overrides.
See the [integration verification report](../docs/testing/claude_integration_verification/REPORT.md).
These checks do not establish exhaustive model quality or AWS deployment readiness.

## Quick start

Team Trailblazer defaults are embedded in `agentic_workflow/shopping_agent/hackathon_config.py`.
The key uses the same reversible XOR/base64 obfuscation as DeepSeek; it is not secure secret storage.
Environment variables override those defaults. No `.env` is needed for the supplied team configuration.
To override settings, optionally copy the configuration from the project root:

```powershell
Copy-Item optional_bedrock/.env.example optional_bedrock/.env
```

Edit `.env` locally to override the URL, team key, and model alias. Blank fields use the embedded defaults.
Environment variables take precedence over `.env`. The root ignore rules exclude `.env`; do not commit
it or include keys in screenshots.

Use the gateway root URL without `/api/chat` or `/v1`. Remote connections require HTTPS.
If the gateway only provides HTTP, forward it to localhost through an organizer-approved SSH connection
and use `http://127.0.0.1:11434`. Even with a local tunnel, the UI explicitly states that data is sent
to cloud-hosted Bedrock.

First, run a low-token connectivity check (**this consumes quota** and does not retry automatically):

```powershell
E:/Software/Miniconda3/python.exe -B -m optional_bedrock --check
```

After confirming `ok: true`, start the small demo catalog with automatic provider selection in terminal one:

```powershell
E:/Software/Miniconda3/python.exe -B -m optional_bedrock --demo --port 8001
```

`--demo` means only that the products are simulated; **LLM calls are still real**. Use this mode to
quickly check requirement extraction and comparisons, and clearly disclose the simulated catalog
during demonstrations. To use the real product index:

```powershell
E:/Software/Miniconda3/python.exe -B -m optional_bedrock --warmup --port 8001
```

Warmup loads the local retrieval models before the server starts listening and does not call the cloud
model. The original project's BM25/PyTorch/Transformers/Parquet dependencies and LFS assets are still required.

In terminal two, start the separate frontend configuration from the project root:

```powershell
cd frontend
npm ci
npm run dev -- --config ../optional_bedrock/vite.config.mjs
```

Open **http://127.0.0.1:5174/**. The separate configuration forwards requests to **8001**, avoiding the
original service on port 8000. The original frontend on port 5173 can keep running. Node/npm must be
installed; this package adds no third-party Python dependencies. On Linux, use the same commands with
your own `python3` or virtual environment interpreter in place of the Windows Python path.

## Integration checklist

1. `/api/health` reports the selected provider: `aws_bedrock_gateway`, `deepseek`, or `off`. Cloud disclosure follows that selection.
2. Enter `I need a blue dress. I prefer cotton.` and inspect the state and actual usage, not just response fluency.
3. Enter `Compare #1 and #2` and inspect `handoff.comparison_assist.status`, evidence, and unknowns in the response.
   Model failures may produce `partial`; HTTP 200 does not guarantee successful generation.
4. Change a preference and compare again to check cache invalidation. Reused results do not count as a new successful model call.
5. `Finalize my selection` only confirms the selection within the session; it does not make a purchase.
6. Simulate an invalid key, timeout, and exhausted quota. Fallback behavior should be explicit and must not appear to be a complete model analysis.

The existing React page does not display all of `personalized_comparison`; this package does not modify
the frontend product code. To inspect the full personalized analysis, check the fields in backend responses
or the built-in demo page, but verify individually which fields are actually displayed.

## Timeouts and quotas

The default timeout for an individual HTTP request is 20 seconds. Model calls within the same outer
chat/handoff, including response generation, share a 35-second waiting budget. Nested comparison
calls do not reset this budget. The writer does not copy the gateway provider or reset its call counter.
The budget excludes a full cold start and is not a strict deadline for total socket time; the original
React client's 45-second overall timeout still needs empirical verification. A busy gateway may produce
partial results; do not retry indefinitely.

Every gateway request uses `options.num_predict = min(requested_max_tokens, 1024)`. The cap applies
to the parser, response writer, and description/comparison calls; smaller connectivity probes keep
their requested limits. Larger settings produced multiple concatenated JSON objects in real tests.
The adapter still rejects concatenated, truncated, or tool-calling responses as a whole rather than
silently taking the first JSON object. Complex responses may still fall back if they exceed this limit.

Each process allows at most 100 cloud requests by default, including failed requests. Once the limit is
reached, the provider returns an explicit error and the existing workflow supplies a fallback result.
This is a request-count safeguard, **not an AWS billing cap**; restarting resets it. Configure it according
to the organizer's usage plan and monitor the official quota separately. Comparisons typically involve
three model stages, and input tokens are also billable. If the gateway omits token counts, results use 0
for compatibility; this does not mean the request was free.

Requests are limited to 60,000 bytes and responses to 1 MB. There are no automatic retries or redirects,
and neither keys nor server error bodies are logged. The starter kit notes that WAF may block large
requests; the default size is not a WAF threshold guaranteed by the organizer. For a 403 response, first
check the key, gateway permissions, and request size, then ask the organizer to confirm the limits.
Do not bypass security rules.

## Intent reversal regression checks

In model-free testing, the initial message `I want a cotton blue shirt` records:
`hard = {category: shirt, color: blue, material: cotton}`.

| Follow-up message | Current model-free result |
|---|---|
| `No cotton` | Remove the hard cotton requirement, exclude cotton as a material, and retain blue and the category |
| `Any material is fine` | Clear the material requirement without excluding cotton |
| `I don't want cotton anymore` | Remove the hard cotton requirement, exclude cotton as a material, and retain blue and the category |
| `wait I don't want cotton anymore, could you recommend me some other materials?` | Same as above; the requirements summary on the right and search filters use the same updated state |

This rule coverage gap has been fixed in the core intent module and does not depend on this optional
package or an LLM. Core regression tests use a simulated catalog containing cotton, linen, and silk,
and check the requirements summary, actual candidates, and Undo behavior.
The full cloud parser accepts evidence-checked `set`, `exclude`, `clear`, `remove_value`, and
`remove_exclusion` operations. Invalid model output falls back to rule-based edits. Keep the exact
messages above as regression cases when testing the real LLM integration.
See `check_intent_reversal.py` for state diagnostics.

## Lightsail deployment scope

Permission to use resources does not mean the organizer has deployed the application for the team.
This package only provides model integration and a separate startup path. Cloud deployment requires
separately deploying the Python backend, building the frontend, and configuring a same-origin `/api`
reverse proxy, HTTPS, and judge access controls. The Vite development server is for local integration
testing and should not serve as the public production entry point. Keep deployment keys in server
environment variables; do not put keys in React or `VITE_*` configuration.

A local run with real search previously took about 53 seconds to cold-start. The product table is nearly
0.9 GB, and loading it into memory alongside the models adds overhead. The starter kit's 4 GB Lightsail
example is not a verified capacity recommendation. Measure resource use and warmup in a CPU environment
first. If necessary, validate the cloud model against a clearly labeled simulated catalog before expanding
to the real catalog. Do not exceed the organizer's resource quota without authorization.

## Testing and removal

```sh
python -B -m unittest discover -s optional_bedrock/tests -v
python -B -m optional_bedrock.check_intent_reversal
```

These tests require no key and do not call AWS. Before removing the package, stop any services it started,
then delete `optional_bedrock/`. No OpenClaw/Hermes installation is required.
Use `python -m agentic_workflow --model-provider off` for offline mode and the original frontend command.
