# Autopilot through Vercel AI Gateway

Autopilot's model calls (Jev, the decision model that plays, and every language model: the agent that writes the
harness, the vision model that describes frames) can all go through **Vercel AI Gateway** with one key.

- **Default whenever a gateway key is there** and runs aren't through Aura. `AUTOPILOT_GATEWAY=0` turns it off; then
  everything works as before (TypeSafe's own API with `TYPESAFE_API_KEY`, the user's own provider keys, or Aura).
- **The key** is looked for in this order: the environment's `AI_GATEWAY_API_KEY`; the settings folder's
  `gateway.env` (`~/Library/Application Support/Autopilot/gateway.env` on macOS, `%LOCALAPPDATA%\Autopilot` on
  Windows, `~/.config/Autopilot` elsewhere; one line `AI_GATEWAY_API_KEY=...`, keep it `chmod 600`); then
  `autopilot.env` in the same folder. Autopilot never writes the key anywhere.
- `AUTOPILOT_GATEWAY_URL` names another gateway root (default `https://ai-gateway.vercel.sh`).
- The code is `autopilot/gateway.py`. Live results are in
  [roblox-live-test-log.md](roblox-live-test-log.md), round 6.

## 1. Inventory: every model call in Autopilot

| Call | Where | Models | Through the gateway |
|---|---|---|---|
| **Jev** decisions (each tick's `system_one`) | `autopilot/runtime.py:105` `jev_client` (`TypeSafeClient`, `:126`); `Runtime.ask` | `jev-1.13.0` (`JEV_MODEL`, `:34`; `AUTOPILOT_JEV_MODEL` overrides) | `base_url=<gateway>/typesafe`, `api_key=<gateway key>` (`gateway.jev()`) |
| Jev keep-warm (`models.list` every 15 s idle) | `runtime.py:163` `_keep_warm` | | works on the gateway (`GET /typesafe/v1/models`, 118 ms) |
| Jev timeouts | `runtime.py:35` `JEV_TIMEOUT = 1.5`, the SDK's `timeout=1.0`, `Runtime.answers` forgets an answer after 2.5 x 1.5 s | | measured p50 0.18 s, p90 0.27 s, max 0.41 s in 286 live decisions: **unchanged** |
| The local stand-in | `runtime.py:70` `StandInJev` (`AUTOPILOT_JEV_STANDIN`) | `local-stand-in` | unchanged (no network) |
| Jev reads a prompt's kind | `autopilot/cli.py:420` `classify` (`jev_client(warm=False)`, 8 s) | Jev | yes (same client) |
| Jev from model-written code | `autopilot/sandbox.py:340` `RemoteJev` -> the parent's client (`:208`) | Jev | yes (the parent's client); the child gets no key, only `AUTOPILOT_JEV_VIA` (`sandbox.child_env`, `:51`) |
| Jev tokens and cost | `autopilot/telemetry.py:93` `CountingJev`, `:111` `decision_cost` | | tokens as before; **cost** from the gateway's `provider_metadata.gateway.cost` |
| Which Jev played | `runtime.py:138` `jev_route` -> the trace's `start` event and the summary's "Decisions by Jev: n (jev-1.13.0, via gateway)" | | new |
| **Claude Code agent** (Claude Agent SDK) | `autopilot/agents/claude_code.py:290` `ClaudeAgentOptions`, `:251` `_env` | `claude-opus-5-5` (`agents/session.py:33` `MODELS`), fast mode on Opus | `ANTHROPIC_BASE_URL=<gateway>` (through the run's proxy), `ANTHROPIC_API_KEY=<gateway key>`; `_env` blanks every other secret, `AI_GATEWAY_API_KEY` included |
| **Codex agent** (openai-codex SDK) | `autopilot/agents/codex.py:78` `api_key`, `:92` `thread_config` (`openai_base_url`), `:271` `login_api_key` | `gpt-6.1-sol` | `OPENAI_BASE_URL=<gateway>/v1`, `OPENAI_API_KEY`/`CODEX_API_KEY=<gateway key>`; the gateway takes the bare id `gpt-6.1-sol` on the Responses API (checked) |
| **OpenCode agent** (embedded, Bun) | `autopilot/agents/opencode.py:39` `PROVIDER_KEYS`, `:133` `host_config`, `:153` `base_url`, `:163` `host_env`; `opencode_host/host.ts:96` | `anthropic`: `claude-opus-5-5`; `openai`: `gpt-6.1-sol`; `google`: `gemini-4-argon`; `deepseek`: `deepseek-v4-pro`; `openrouter`: `anthropic/claude-opus-5.5`; `opencode` (Zen): `claude-opus-5-5` | Anthropic: its own provider with the gateway as base URL (`<gateway>/v1`). Every other provider: switched to models.dev's **`vercel`** provider (the AI SDK's gateway provider, key `AI_GATEWAY_API_KEY`), the model named `<provider>/<model>` (`gateway.Run`). UNVERIFIED live (no Bun on the test machine) |
| The user's own agent (`autopilot mcp`, `connect claude/codex`) | `autopilot/agents/external.py`; `cli.py:887` `decision_key_missing`; `autopilot/connect.py:271` | the user's | not ours: the user's agent keeps its own login. Jev and the describer fallback go through the gateway |
| **Vision**: describe_frames | `autopilot/vision.py:86` `route`, `:103` `gateway_route`, `:316` `_anthropic`, `:337` `_openai` | `MODEL = FALLBACK = claude-opus-5-5`, or the agent's model where it takes pictures | new provider `"gateway"` (`vision.GATEWAY`): a Claude model on Anthropic's wire at `<gateway>`, any other on chat completions at `<gateway>/v1`; DeepSeek (no pictures) gives way to Claude; a model the gateway refuses falls back to Claude for the rest of the run. `cli.run_prompt` picks it whenever the gateway is on (`cli.py:674`) |
| describe_frames fallback for the user's agent | `external.py:113` `FALLBACK_PROVIDERS`, `:118` | Anthropic first | the gateway route (same as above) |
| The LLM proxy (token counting, `llm.jsonl`) | `telemetry.py:258` `LLMProxy`, `:473` `_route`, `UPSTREAMS` `:36` | | its upstream is `<PROVIDER>_BASE_URL`, i.e. the gateway; usage parsed as before, plus the gateway's `usage.cost` on chat completions (`llm_usage`, `:135`) |
| Aura's relay | `autopilot/aura.py:61` `RUN_ENV`, `:327` `__enter__`, `:335` `_agent_env` | | **never** with the gateway: a run through Aura uses Aura's relay as before (`gateway.enabled()` is false whenever `AUTOPILOT_AURA_URL` is set) |
| Account frame (plugins' Settings) | `autopilot/account.py:276` `state`, `:155` `masked` | | `mode: "own_keys"`, new `via: "gateway"`, `keys.gateway: "••••"` (no character of the key shown) |
| Redaction | `autopilot/redact.py` (`Redactor`), `agents/session.py:1632` `scrub` | | the gateway key by value (wherever it is kept) and by shape (`vck_...`) |
| research.py, words.py, mcp.py | | | no model calls (research helpers are greps over the codex; words.py rewrites text; mcp.py relays to `autopilot serve`) |

## 2. Models on the gateway

Checked 2026-10-31 with `tools/gateway_check.py` (authorized `GET /v1/models`: 499 models; tiny live calls, max 20
tokens; one 22 KB game frame for the models that take pictures). Raw results:
[roblox-live-evidence/gateway/model-check.json](roblox-live-evidence/gateway/model-check.json).

| Autopilot model id | Gateway id | Listed? | Live call ok? | Notes / substitution |
|---|---|---|---|---|
| `jev-1.13.0` (Jev) | `jev-1.13.0` at `/typesafe` (canonical `typesafe-ai/jev-1.13.0`) | yes (`typesafe-ai/jev-1.13.0`; also `typesafe-ai/jev`, `typesafe-ai/jev-1.14.0`) | yes: 20/20 calls each with `jev-1.13.0` and `typesafe-ai/jev-1.13.0`, p50 165 ms, p90 220 ms; `models.list` (keep-warm) 118 ms | cost in the answer: $0.042 per 1M input tokens ($0.000013 for a 308-token call) |
| `claude-opus-5-5` (agent, vision) | `claude-opus-5-5` or `anthropic/claude-opus-5.5` | yes (`anthropic/claude-opus-5.5`) | yes: Messages API (text and picture, `output_config.effort` accepted), chat completions (text and picture); Claude Agent SDK / Claude Code with `ANTHROPIC_API_KEY`, standard and fast mode | the Messages API doesn't say a cost; chat completions do |
| `gpt-6.1-sol` (Codex, OpenCode openai) | `openai/gpt-6.1-sol` (the bare id works too) | yes | yes: chat completions (text and picture), Responses API (bare and prefixed id) | |
| `gemini-4-argon` (OpenCode google) | `google/gemini-4-argon` | yes | yes: chat completions (text and picture), 3.9 s | slowest of the set |
| `deepseek-v4-pro` (OpenCode deepseek) | `deepseek/deepseek-v4-pro` | yes | yes: chat completions (text) | takes no pictures: frames are described by Claude |
| `anthropic/claude-opus-5.5` (OpenCode openrouter) | `anthropic/claude-opus-5.5` | yes | yes | |

Every model Autopilot names is on the gateway: **no substitution was needed**. A model not in `gateway.MODELS` is
named `<provider>/<model>` (`gateway.model_id`).

## 3. How it is wired

- `gateway.enabled()`: a key, `AUTOPILOT_GATEWAY` not `0`, and not Aura.
- **Jev**: `runtime.jev_client()` passes `gateway.jev()` (`api_key`, `base_url=<gateway>/typesafe`) to the SDK, so
  every Jev client (runs, `classify`, `autopilot run`) uses it, inside a run or not. No compression (as before).
- **A run** (`cli.run_prompt`, `cli.cmd_run`) is wrapped in `gateway.run()` when not through Aura: like `aura.Run`,
  it sets `TYPESAFE_*`, `ANTHROPIC_BASE_URL/_API_KEY` (and drops `ANTHROPIC_AUTH_TOKEN`/`_OAUTH_TOKEN`),
  `OPENAI_BASE_URL/_API_KEY`, `CODEX_API_KEY` and `AI_GATEWAY_API_KEY` for the run only, switches OpenCode's
  non-Anthropic providers to `vercel`, and puts everything back after. The run's telemetry proxy then routes
  `anthropic` and `openai` to the gateway and counts the calls.
- **Secrets**: Claude Code gets only `ANTHROPIC_API_KEY` (the gateway key) and the base URL; Codex gets nothing in its
  environment (it signs in with `login_api_key`); OpenCode gets only its provider's key; the sandbox child gets no key
  (only `AUTOPILOT_JEV_VIA=gateway`, for its trace). The key is scrubbed from what the plugin is sent and redacted from
  shared run data by value and by its `vck_` shape. Tests never read the machine's key (`tests/conftest.py`).
- **Usage and cost**: Jev's cost from `provider_metadata.gateway.cost`, chat completions' from `usage.cost`; the
  `usage` message gains `cost_usd` per model where known; each run prints one `Usage:` line to the agent service's log.
- **`decision_key_missing`** no longer asks for a TypeSafe key when there is a gateway key; its text names both.

## 4. Turning it off, and the other paths

| You want | Do |
|---|---|
| Jev on TypeSafe's own API | `AUTOPILOT_GATEWAY=0` and `TYPESAFE_API_KEY` |
| Your own provider keys for the agent | `AUTOPILOT_GATEWAY=0` (with the gateway on, the agent's key is the gateway's) |
| Aura | as before (`--aura`, `AUTOPILOT_AURA_URL`, or the setup's choice): the gateway is never used then |
| The local stand-in | `AUTOPILOT_JEV_STANDIN=random` (unchanged) |

## 5. Known limits

- OpenCode through the gateway is wired and unit-tested but **not checked live** (no Bun on the test machine);
  whether OpenCode's model catalog has every `<provider>/<model>` under `vercel` is unverified. Its Anthropic path
  uses the same base-URL mechanism as the proxy and Aura's relay.
- Codex through the gateway is unit-tested only (the codex extra isn't installed here); the gateway's Responses API
  answered both `gpt-6.1-sol` and `openai/gpt-6.1-sol`.
- The Anthropic Messages API on the gateway doesn't return a cost; Claude Code's own `cost_usd` estimate is in the
  report, and tokens are counted by the proxy.
- The account frame keeps `mode: "own_keys"` (the plugins know three modes; another reads as "none" and would show
  their first-run setup again) and says the gateway in `via`.
