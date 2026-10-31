# Autopilot for Roblox Studio

> **Experimental and UNVERIFIED.** This plugin was written and checked offline (selene, stylua, a Rojo build, and
> the Python tests' fake plugin against the real bridge). It has not been run in Roblox Studio yet. See
> [docs/roblox.md](../../docs/roblox.md) for the design, the install steps and the known limits.

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

Studio then asks whether Autopilot may make HTTP requests to `127.0.0.1`: allow it. Turn on **Game Settings >
Security > Allow HTTP Requests** for the place too (the plugin's play-server half may need it: UNVERIFIED).

## How it works

A Studio plugin runs once per DataModel. Here:

- **edit** (`EditHost.luau`): the toolbar button and dock (`Dock.luau`), the session with the agent service (`hello`),
  `session start` (puts the client agent in the place, `StudioTestService:ExecutePlayModeAsync`), the codex
  (`Codex.luau`).
- **play server** (`PlayServer.luau`): attaches to the same session (the play test's arguments name it) and answers
  the game's requests; `session stop` calls `StudioTestService:EndTest`. Heartbeats, `log` events and `log_error`
  findings from `LogService`.
- **play client**: the client agent (`ClientAgent.luau`, copied into `StarterPlayerScripts` as a LocalScript for the
  run, with the modules it needs) answers camera, input and GUI requests the server passes on through
  `ReplicatedStorage.AutopilotAgentRemote`.

## Source map

All under `src/`.

| File | What it does |
|---|---|
| `Main.server.luau` | The entry: EditHost in the edit DataModel, PlayServer in a play test's server. |
| `Config.luau` | The bridge's address, the token (baked in by `autopilot roblox install`), limits, names. |
| `Link.luau` | The HTTP link: hello / attach, a long poll, batched sends, a rate limit (8 requests a second). |
| `Json.luau` | JSON that keeps `{}` and `[]` apart and writes null. |
| `EditHost.luau`, `Dock.luau` | The edit DataModel's half and its dock. |
| `Agent.luau`, `Sources.luau` | Putting the client agent in the place for a run and taking it out. |
| `PlayServer.luau` | The play server's half: dispatch, the relay to the client agent, logs, heartbeats. |
| `ClientAgent.luau` | The client agent (a LocalScript's source; not run by the plugin itself). |
| `World.luau` | The player, `find`, `transforms`, `pose`, `movement` (kit frame). |
| `Sight.luau` | `view`, `watch`. |
| `Input.luau` | `key`, `input`, `move`: semantic input (Humanoid:Move, Jump, the camera, ProximityPrompts, Tools, ContextActionService). |
| `Gui.luau` | `screen`, `ui`, `ui_act` (no clicks: 501). |
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

The tool versions are in `rokit.toml`. There are no in-Studio tests yet.
