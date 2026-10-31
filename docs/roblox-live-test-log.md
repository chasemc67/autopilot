# Roblox Studio support: live test log

Machine: Chase's Mac Mini, macOS 27.0.1, Roblox Studio 0.745.0.7451137, a NEW unsaved Baseplate ("Place1") only.
Nothing was saved or published. Evidence screenshots: [roblox-live-evidence/](roblox-live-evidence/).

Tools used throughout:

- `tools/roblox_probe.py serve` runs the real HTTP bridge (`autopilot/roblox_bridge.py`) without the agent service
  and logs every frame the plugin sends; `tools/roblox_probe.py call <op> '<json args>'` sends the plugin one request
  and prints the answer (with its round-trip time).
- `tools/roblox_test_place.luau`: the test game (5 coins, leaderstats, a door that opens at 5 coins, a goal pad, a
  lever with a ProximityPrompt, an F-key ContextActionService action, and two planted bugs: Coin3 is worth 2, the
  sign's script errors). Run in the edit DataModel through the Studio MCP's `execute_luau`.
- The Studio MCP (`StudioMCP`, Roblox's own MCP server) for probing the edit / server / client DataModels.

## Round 1: 2026-10-31 12:30-13:05 PDT: install, handshake, first live probes

### Install (stage 1)

```
$ autopilot roblox install
Installed the Autopilot plugin: /Users/chase-mini/Documents/Roblox/Plugins/Autopilot.server.lua
$ autopilot serve > /tmp/autopilot-serve.log 2>&1 &   # (background)
$ autopilot roblox status
Plugin: /Users/chase-mini/Documents/Roblox/Plugins/Autopilot.server.lua (this machine's token baked in)
Bridge: http://127.0.0.1:8766/v1/roblox: answering (`autopilot serve` is running)
```

- Studio did **not** load a new `.lua` file dropped in the Plugins folder while running, and does **not** reload a
  changed one (touched, rewritten, Studio focused: no new hello). A Studio restart is needed after every install.
  (`RojoManagedPlugin.rbxm` left alone.)
- Quitting Studio: SIGTERM is ignored; `osascript -e 'tell application "RobloxStudio" to quit'` raises "Save changes
  to Place1?": **Don't Save**. Relaunched with `open /Applications/RobloxStudio.app`, new Baseplate from "Open a
  Template".
- After the restart the **Autopilot** button is in the Plugins tab ([01-toolbar-button.png](roblox-live-evidence/01-toolbar-button.png)).
  Opening the dock: "Connected to the agent service" ([02-dock-connected.png](roblox-live-evidence/02-dock-connected.png)).
- **No HTTP permission prompt appeared.** A local plugin file's `HttpService:RequestAsync` to `127.0.0.1` simply
  worked, with `HttpService.HttpEnabled == false` in the place. (The Studio MCP's own `execute_luau`, which is not a
  plugin, gets "HTTP requests are not enabled".)

Verdict: install VERIFIED (with a Studio restart); handshake VERIFIED (`hello` arrived with `engine: roblox`,
`project: Place1`, `resume` of the kept session). Bug: `engine_version` was `""` (`RunService:GetRobloxVersion` isn't
callable by a plugin): fixed to use `version()`.

### Assumptions, one by one (stage 2)

All through `tools/roblox_probe.py call ...` against the real plugin. Round trips are 0.14-0.26 s.

| Assumption | Probe | Result |
|---|---|---|
| Edit ops answer | `context`, `ping`, `session status`, `codex_manifest` | VERIFIED (codex: `AutopilotCodex/summary.txt`, 2.5 KB for the empty baseplate) |
| `StudioTestService:ExecutePlayModeAsync` from the edit plugin starts Play | `session start` | VERIFIED: Play starts (Client and Server tabs) ([03-play-started-by-autopilot.png](roblox-live-evidence/03-play-started-by-autopilot.png)) |
| The plugin runs in the play **server** DataModel and `GetTestArgs()` carries the session | sessions after start: `server` channel attached | VERIFIED |
| HTTP from the play server to localhost needs Allow HTTP Requests | the server channel attached with `HttpEnabled = false` | NOT NEEDED: plugins may call localhost without it |
| A `LocalScript` the edit plugin puts in StarterPlayerScripts (Source set by the plugin) runs in the client | `context` → `client_agent: true`; `pose` answered by the client | VERIFIED |
| `Humanoid:Move` walking | `move {"direction":{"X":1},"seconds":1.5}` → `moved: 669 cm` (WalkSpeed 16 st/s = 448 cm/s) | VERIFIED |
| Held movement key | `key W hold seconds=1` → 440 cm further | VERIFIED |
| Jump | `key SpaceBar` → feet at 209 cm, `on_ground: false`, then back down (JumpHeight 7.2 st ≈ 202 cm) | VERIFIED |
| Camera turn by setting `Camera.CFrame` | `key MouseX value=300` → camera yaw 0 → 90 (0.3°/count) | VERIFIED |
| `ProximityPrompt:InputHoldBegin/End` from a client script | lever's prompt → server's `Triggered` fired (`Pulled = true`) | VERIFIED |
| `ContextActionService:CallFunction` from a client script | "The current thread cannot call 'CallFunction' (lacking capability RobloxScript)" | **FAILS** |
| `VirtualInputManager:SendKeyEvent` | "lacking capability RobloxScript" | **FAILS** (as expected) |
| `LogService.MessageOut` on server and client | a server `error()` and a client `error()` → `log` events (`category` server/client) and `log_error` findings | VERIFIED |
| Heartbeats | 46 heartbeats in ~46 s | VERIFIED |
| `rc` property read / write / call / describe | SpawnLocation's properties; `Transparency` write; `Workspace:FindFirstChild("Baseplate")` → `/Workspace/Baseplate` | VERIFIED |
| `watch`, `view`, `screen`, `ui` | answer (empty on an empty baseplate) | answer; rechecked with the test game |
| `session stop` → play server `StudioTestService:EndTest` | Studio back in Edit, `server` channel gone, temporary objects removed (0 tagged) | VERIFIED |
| `session restart` | stop OK, then "The play test started but its server didn't reach Autopilot within 45 s" | **FAILS**: see below |
| Studio's own Play (not Autopilot's) joins the dock's session | server channel attached; `pose` → 409 "the client agent isn't in this play test" | works as designed, but no client agent |

**Restart bug.** The bridge asks the edit plugin to start again as soon as the server channel has said bye, but
`ExecutePlayModeAsync` returns in the edit DataModel a moment later, so the edit plugin still had `testRunning = true`
and answered "running" without starting. Fix: `startTest` waits up to 20 s for the last test to end.

### New findings that change the design

1. **A play server can inject the client agent at runtime.** From the server DataModel (plugin-level code), a new
   `LocalScript` whose `Source` is set there, with a `ModuleScript` child, parented to the player's `PlayerGui`,
   replicates **with its source** and runs on the client (`ProbeRan` attribute set by the client; its module
   required). So the edit plugin need not touch the place at all: no temporary objects in the edit DataModel, nothing
   that could be saved with the place, and Studio's own Play button gets the client agent too.
2. **Roblox's own Studio MCP server (`/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP`, stdio) is reachable
   from the host** (a Python client: `initialize`, `tools/call`; init 0.01 s). It gives what Luau can't:
   - `screen_capture`: a JPEG of the game viewport during Play, 0.28-0.38 s, 1292x648, **with Studio in the
     background** (Terminal frontmost) and live (the picture follows the character).
   - `user_keyboard_input`: real key events in the play client: `F` fired the place's ContextActionService `Wave`
     action (`Waved` 1 → 3), with Studio in the background.
   - `user_mouse_input` with an `instance_path`: a real click on a `TextButton` in PlayerGui fired `Activated`
     (`ShopClicks = 1`).

Verdict of the round: the core of the design works live. Fixes and redesign in round 2.

## Round 2: 2026-10-31 13:05-13:55 PDT: redesign, re-verify

Fixes and redesign (commits `4f35b87`, `6b97532`), after a Studio restart with the new plugin:

- **Client agent injected at runtime by the play server** (`Agent.luau`, `PlayServer.luau`): the remotes in the play
  test's ReplicatedStorage, the agent LocalScript (with its modules) in a `ResetOnSpawn = false` ScreenGui in each
  player's PlayerGui. The edit plugin no longer puts anything in the place.
- **Pictures, recording and real input from Roblox Studio's MCP server** (`autopilot/studio_mcp.py`, used by
  `roblox_bridge.Session`): `capture` and `record` are the host's (`screen_capture`); `key` for every key but the
  semantic ones (WASD/arrows, SpaceBar, mouse axes and buttons), `ui_act` clicks and ContextActionService `input`
  actions become real key / mouse events (`user_keyboard_input`, `user_mouse_input` with an `instance_path`).
- **Early errors**: `LogService:GetLogHistory()` replayed when the play server half and the client agent start.
- Restart fix (round 1) and `engine_version`.

Live checks (`tools/roblox_probe.py`, the test place built with `tools/roblox_probe.py luau tools/roblox_test_place.luau`):

| Check | Command | Result |
|---|---|---|
| Agent injected at runtime | `session start`; `context` | `client_agent: true`; PlayerGui holds `Freecam, Hud, AutopilotClientAgent` | 
| GUI click | `ui_act {"id":"Hud.ShopButton","action":"click"}` | `{"clicked": "Hud.ShopButton", "how": "a real mouse click ..."}` in 0.73 s; `screen` → `Hud.Status: "The shop is closed today"`; `ShopClicks = 1` |
| CAS action | `input {"action":"Wave"}` | `{"did": "real key F (action Wave)"}`; `Waved = 2` (with `key F`) |
| ProximityPrompt by real key | `move` to the lever, `key E` | `{"did": "real key E"}`; `Lever.Pulled == true` |
| Capture | `capture {"max_dim":1280}` | 1292x648 JPEG in 0.28 s: [04-capture-during-play.jpg](roblox-live-evidence/04-capture-during-play.jpg) |
| Record | `record start fps=4`, 3 s, `record stop` | 12 `frame`s fed to the link (4 fps) |
| `view` with the test place | `view` | the Sign and SpawnLocation with distances, screen positions, sizes |
| Codex during play | `codex_manifest` | the three test scripts + summary |
| Restart | `session restart` | `{"running": true, "restarted": true}` in 1.65 s |
| Edit place untouched | after `session stop`: tagged objects, StarterPlayerScripts, ReplicatedStorage | `tagged=0 SPS=[TestPlaceClient] RS=[]` (only the test place's own script) |
| Early error | `SignText:3: attempt to index nil` at game start | **missing** from the findings before the fix (it is in `GetLogHistory()`): fixed by the replay |

Other findings this round:

- `autopilot connect claude` writes the **user-scope** Claude Code config (an MCP server for every project, a skill and
  a subagent in `~/.claude`). On this shared machine that would change every agent's Claude Code, and the task allows
  changes inside the repository only. The e2e runs load **the same files** (`python -m autopilot.connect plugin
  .e2e/plugin`, which the tests check is what `connect claude` installs) for one session with `claude --plugin-dir`.
- Runs need Jev (TypeSafe's hosted decision model, `TYPESAFE_API_KEY`) or Aura; neither is available here. Added a
  clearly labelled **local stand-in** (`AUTOPILOT_JEV_STANDIN=random|first`, `runtime.StandInJev`): it picks options
  at random and never reads the state. Everything else in the e2e runs is real.
- Run data is shared by default (`hello.share` absent = shared, which syncs memory with Aura). The Roblox plugin has
  no consent UI, so its hello now says `share: false`; the e2e runs also set `AUTOPILOT_SHARE=0`.
