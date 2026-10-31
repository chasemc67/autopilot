# Autopilot for Roblox Studio

> **Experimental; verified live** in Roblox Studio 0.745 on macOS (see
> [docs/roblox-live-test-log.md](../../docs/roblox-live-test-log.md)). See [docs/roblox.md](../../docs/roblox.md) for
> the design, the install steps and the known limits.

The plugin lets the Autopilot agent play a Roblox place in Studio. It reaches the agent service (`autopilot serve`)
over HTTP: a Studio plugin can't hold a WebSocket or listen, so it long-polls the service's Roblox bridge
(`autopilot/roblox_bridge.py`, `http://127.0.0.1:8766/v1/roblox`).

## Install

From the repository root:

```bash
autopilot roblox install      # builds src/ into Autopilot.server.lua with this computer's token and copies it into
                              # Studio's Plugins folder
autopilot serve               # the agent service; then open the Autopilot dock in Studio (Plugins tab)
```

Other ways to build it:

- `python plugin/roblox/build_plugin.py --out <Plugins>/Autopilot.server.lua [--token <token>]` (no token: paste it
  into the dock's Token field; `autopilot roblox status --show-token` prints it).
- `rojo build plugin/roblox/default.project.json -o <Plugins>/Autopilot.rbxm` (Rojo 7). No token baked in either.

Restart Studio after installing (it doesn't load new or changed plugin files while it runs). No HTTP permission or
**Allow HTTP Requests** setting was needed when checked live; should Studio ask whether Autopilot may make HTTP
requests to `127.0.0.1`, allow it.

## How it works

A Studio plugin runs once per DataModel. Here:

- **edit** (`EditHost.luau`): the toolbar button and dock (`Dock.luau`), the session with the agent service (`hello`),
  `session start` (`StudioTestService:ExecutePlayModeAsync`), the codex (`Codex.luau`). It never changes the place.
- **play server** (`PlayServer.luau`): attaches to the same session (the play test's arguments name it, or the dock's
  session for Studio's own Play button), puts the client agent in each player's PlayerGui (`Agent.luau`) and answers
  the game's requests; `session stop` calls `StudioTestService:EndTest`. Heartbeats, `log` events and `log_error`
  findings from `LogService` (start-up ones from its history).
- **play client**: the client agent (`ClientAgent.luau`, a LocalScript the play server makes at runtime, with the
  modules it needs) answers camera, input and GUI requests the server passes on through
  `ReplicatedStorage.AutopilotAgentRemote`.
- **the host** (`autopilot/roblox_bridge.py`, `autopilot/studio_mcp.py`): pictures, recording, real keys and GUI
  clicks through Roblox Studio's own MCP server.

## Source map

All under `src/`.

| File | What it does |
|---|---|
| `Main.server.luau` | The entry: EditHost in the edit DataModel, PlayServer in a play test's server. |
| `Config.luau` | The bridge's address, the token (baked in by `autopilot roblox install`), limits, names. |
| `Link.luau` | The HTTP link: hello / attach, a long poll that carries the answers, a token bucket (Studio's HttpService budget: 7 requests a second). |
| `Json.luau` | JSON that keeps `{}` and `[]` apart and writes null. |
| `EditHost.luau`, `Dock.luau` | The edit DataModel's half and its dock. |
| `Agent.luau`, `Sources.luau` | Putting the client agent in the play test (from the play server, at runtime). |
| `PlayServer.luau` | The play server's half: dispatch, the relay to the client agent, logs, heartbeats. |
| `ClientAgent.luau` | The client agent (a LocalScript's source; not run by the plugin itself). |
| `World.luau` | The player, `find`, `transforms`, `pose`, `movement` (kit frame). |
| `Sight.luau` | `view`, `watch`. |
| `Input.luau` | `key`, `input`, `move`: semantic input (Humanoid:Move, Jump, the camera, ProximityPrompts, Tools, ContextActionService). |
| `Gui.luau` | `screen`, `ui`, `ui_act` (a click names the button for the host's real click). |
| `Rc.luau`, `Values.luau`, `Paths.luau`, `Frame.luau` | `rc`; Roblox values as JSON; instance paths; the kit frame (1 stud = 28 cm). |
| `Codex.luau` | `codex_manifest`, `codex_read`: every script's text and a summary of the place. |
| `build_plugin.py` | The single-file build. |

## Checks

```bash
cd plugin/roblox
stylua --check src            # formatting (stylua.toml)
selene src                    # lint (selene.toml, Roblox std)
rojo build default.project.json -o /tmp/Autopilot.rbxm
cd ../.. && python -m pytest tests/test_roblox.py
```

The tool versions are in `rokit.toml`. Live, in Studio: `tools/roblox_probe.py` (the bridge without the agent service,
one request at a time), `tools/roblox_latency.py`, and the test game `tools/roblox_test_place.luau`
(docs/roblox-live-test-log.md).
