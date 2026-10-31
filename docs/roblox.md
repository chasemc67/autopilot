# Autopilot for Roblox Studio

> **Status: experimental, UNVERIFIED.** Everything on the Roblox side of this document (the plugin's Luau, the Studio
> APIs it calls, the HTTP bridge as seen from Studio) was written and unit-tested offline only. It has **not** been run
> against a real Roblox Studio yet. Statements below that depend on Studio's behaviour and that nobody has checked are
> marked **UNVERIFIED**. The Python half (the host bridge, the engine adapter, the CLI) is covered by the test suite.

Roblox Studio is the fourth engine next to Unreal 5, Unity 6 and Godot 4. This page is the design note (what maps to
what, and why) followed by the install and usage instructions.

## 1. How the other engines talk to Autopilot

Read this first if you are new to the repository; it is what the Roblox port has to fit.

- **Transport.** Every plugin connects *out* to the brain (`autopilot serve`) over a WebSocket at
  `ws://127.0.0.1:8765/v1/bridge` (`autopilot/bridge.py`, `serve_local`). The brain announces its address in
  `Autopilot/brain.json` in the user settings folder.
- **Handshake.** The plugin's first frame is `hello` (`protocol: 1`, `engine`, `project`, `host: editor|game`,
  `features`, optional `run`, `resume`, `share`, `project_id`). The brain answers `welcome` (`role`, `session`,
  `features`). An optional `Authorization: Bearer` token is read for runs through Aura; local runs have no auth beyond
  "it's on loopback".
- **Messages.** JSON frames with a `type`:
  - brain → plugin: `request {id, op, args}`, `status`, `result`, `run_kind`, `memory_state`, `account`, `activity`, ...
  - plugin → brain: `response {id, ok, result | status+error, t}`, `events {events: [heartbeat | log | finding |
    session]}`, `start {run}`, `stop`, `steer {text}`, `clear_memory`, `frame` (recording), ...
  - The brain wraps one connection in a `bridge.Link` (`request`, `post`, `feed`, `close`). Everything above the link
    (cli, agents, kit, runtime) is engine-neutral.
- **Ops** (the requests a plugin answers): `context`, `session {status|start|stop|restart}`, `ping`, `rc
  {probe|property|call|describe}`, `screen`, `ui`, `ui_act`, `view`, `watch`, `key`, `input`, `move`, `pause`,
  `timestep`, `detectors`, `record`, `capture`, `codex_manifest`, `codex_read`; the Unity and Godot plugins also answer
  the engine-neutral `find`, `transforms`, `pose`, `movement` (`bridge.ENGINE_OPS`) in the **kit frame** (X forward,
  Y right, Z up, centimetres, yaw turning right, pitch up).
- **Play mode.** The brain asks `session start` (Unreal PIE, Unity play mode, Godot's `play_main_scene`) and `session
  stop` once the run is over. The Godot editor holds the socket and relays everything else to the game process over
  the debugger channel; the Roblox design copies that split.
- **State and source.** Reads go through `rc` (native units) and the neutral ops (kit units). The codex (the game's
  code as text) is fetched from the plugin (`codex_manifest`, `codex_read`) or copied from disk
  (`Engine.copy_source`).
- **Input.** `key` (Unreal FKey names, press / hold with a lease / release / release_all, `MouseX`/`MouseY` axes),
  `input` (a named input action), `move` (walk in a kit direction for N seconds), `ui_act` (click a widget).
- **Pictures.** `capture` (one JPEG, base64) and `record` (a stream of `frame` messages). Optional: `hello.features`
  says `media` when the plugin can.
- **Reports.** The brain sends `result` (and files) back to the plugin; logs and findings flow the other way as
  `events`.
- **Engine registration.** `autopilot/engines/__init__.py` (`ENGINES`), one `Engine` subclass per engine (project on
  disk, codex rules, kit-frame conversion, the engine's section of the kit reference, `<engine>.md`). `hello.engine`
  picks it.
- **`autopilot connect claude|codex`** installs an MCP server (`autopilot mcp`) and a skill into the user's own agent;
  the MCP server talks to the running `autopilot serve` through `brain.json`'s `control` URL. It is engine-neutral, so
  Roblox gets it for free once `autopilot serve` can see a Roblox plugin.
- **Packaging.** `tools/package_release.py` zips each engine's plugin folder; the Python package ships
  `engines/*.md`.

## 2. Limits of a Roblox Studio plugin

| Limit | Consequence for Autopilot |
|---|---|
| A plugin can't open sockets or listen. `HttpService:RequestAsync` is its only way out (HTTP/HTTPS, JSON in strings). (`HttpService:CreateWebStreamClient` exists for WebSocket/SSE, but its availability to plugins and to localhost is UNVERIFIED, so it is not used.) | The brain gets a second, **HTTP long-poll** front door (`autopilot/roblox_bridge.py`) next to the WebSocket. The plugin polls it. |
| Plugin HTTP to `localhost` needs the plugin's **HTTP Requests** permission (Studio prompts once per domain). In a play session's server, `HttpService.HttpEnabled` (Game Settings > Security > **Allow HTTP Requests**) may also be required (UNVERIFIED). | Install step: allow both. |
| Game servers are limited to about 500 HTTP requests a minute (UNVERIFIED whether this applies to a plugin in Studio's play server). | The plugin batches frames and piggybacks its outgoing frames on its polls; it caps itself at 8 requests a second. |
| Plugin `Script`s run once **per DataModel**: the edit DataModel, and during a play test also the play **server** and the play **client** (`RunService:IsEdit/IsServer/IsClient`). They can't share Lua state. | The edit instance is the "editor" (it says `hello`, owns the run UI, starts play, serves the codex). The play-server instance attaches to the same session as a second channel and answers the game ops. The host routes each request to the right channel. |
| No OS-level input from Luau. `VirtualInputManager` is RobloxScriptSecurity: not callable by plugins (UNVERIFIED that this holds in every Studio build). `UserInputService` events can't be faked. | Input is **semantic**, done by a client agent `LocalScript`: movement keys become `Humanoid:Move`, `SpaceBar` sets `Humanoid.Jump`, mouse axes turn the `Camera`, `E`-style keys trigger the nearest `ProximityPrompt` (`InputHoldBegin/End`), `LeftMouseButton` activates the equipped `Tool`, and any key or action bound through `ContextActionService` is called with `ContextActionService:CallFunction`. A game that reads raw `UserInputService.InputBegan` won't see these keys: the agent is told so (`autopilot/engines/roblox.md`). |
| No screen capture from Luau (no API reads pixels; `CaptureService` produces a content id, not bytes). | `capture` is answered **on the host**, optionally: with `AUTOPILOT_ROBLOX_SCREENSHOT=1` the bridge takes an OS screenshot (macOS `screencapture`, Windows PowerShell). Off by default (it grabs the whole screen); then `capture` answers 409 "no picture". `record` is not supported (409). |
| GUI buttons can't be clicked from Luau: there is no `GuiButton:Activate()`, and `Activated` can't be fired. | `ui` lists buttons, text boxes and their texts; `ui_act` `set` changes a `TextBox`'s text (no `FocusLost` fires); **clicks answer 501** with an explanation the agent reads. An UNVERIFIED alternative for later: `GuiService.SelectedObject` plus gamepad emulation (needs VirtualInputManager). |
| A plugin can't read the place's file path; an unpublished place has `PlaceId` 0. | `context.project_dir` is empty; the codex comes from the plugin's export (all scripts' `Source`), or from a Rojo project / `.rbxlx` on disk given with `--project`. |
| `StudioTestService:ExecutePlayModeAsync(args)` starts a play test from the edit DataModel and yields until it ends; `StudioTestService:EndTest(result)` ends it and must be called from the play **server**; `GetTestArgs()` reads `args` there. (Documented API; behaviour as described here is UNVERIFIED.) | `session start` → edit plugin calls `ExecutePlayModeAsync({session=...})`; the play-server plugin reads the session from `GetTestArgs()` and attaches. `session stop` is routed to the play server, which calls `EndTest`. `restart` = stop, wait for the play channel to go, start. |
| Running scripts can't be paused from Luau. | `pause` answers `{"paused": false, "note": ...}` (no-op), as Godot's `timestep` note does. |

## 3. What maps to what

| Autopilot | Roblox Studio |
|---|---|
| project | the open place (`game.Name`); on disk optionally a Rojo project (`default.project.json`) or a `.rbxlx`/`.rbxl` file |
| level / map | one place = one level. `session restart` with a `map` other than the current place answers 400. |
| play (PIE) | a Studio play test (`StudioTestService:ExecutePlayModeAsync`), one local player |
| object path | `/Service/Child/Grandchild`, a duplicate name gets `@n` (2nd, 3rd... sibling of that name, as Unity's paths): `/Workspace/Coins/Coin@2` |
| class (`game.actors(cls)`) | `Instance:IsA(cls)` (`"BasePart"`, `"Model"`), else a CollectionService tag with `tag:<name>`, else an exact `Name` |
| actors (`all_actors`) | Workspace's direct children and the Models / BaseParts in Folders under it (not every part of a model) |
| player | `Players.LocalPlayer.Character` (its `HumanoidRootPart`) |
| frame | Roblox is Y up, right-handed, -Z forward, studs. **1 stud = 28 cm** (Roblox's own physics conversion). kit `(X, Y, Z) = 28 * (-z, x, y)`; kit yaw = `-deg(ry)`, pitch = `deg(rx)` of `CFrame:ToOrientation()` (YXZ). Same axes as Godot, different scale. |
| movement numbers | `Humanoid.WalkSpeed`, `JumpPower` (or `JumpHeight` → `sqrt(2 g h)`), `Workspace.Gravity`, `HipHeight` + root part size, `MaxSlopeAngle`; step: none (Humanoids climb small ledges by themselves; reported as null) |
| `rc property` | instance properties by name (a curated list per class for "all"), plus **attributes** (`GetAttributes`) and the `Value` of `ValueBase` children (leaderstats) |
| `rc call` | a method on an instance; Roblox methods take positional arguments, so they come as a list: `game.call(path, "FindFirstChild", args=["Door"])` (a JSON object loses its order on the way through `HttpService:JSONDecode`) |
| `screen` | visible `TextLabel` / `TextButton` / `TextBox` texts under `PlayerGui`, keyed `<ScreenGui>.<Name>` |
| `ui` / `ui_act` | buttons and text boxes in `PlayerGui`; `ui_act` `set` on a TextBox; clicks: 501 (see limits) |
| `key` / `input` / `move` | the client agent's semantic input (see limits) |
| `view` | parts / models in front of the camera (`Camera:WorldToViewportPoint`) with a line of sight (`Workspace:Raycast`) |
| `watch` | positions and `Transparency` sampled for N seconds on the server |
| logs / findings | `LogService.MessageOut` (server and, through the client agent, client): `log` events and `log_error` findings |
| heartbeat, game time | play server, every second; `t` = `workspace.DistributedGameTime` |
| codex | every `LuaSourceContainer`'s `Source` as `scripts/<Service>/<path>.server.luau|.client.luau|.luau`, plus `summary.txt` (services' trees, RemoteEvents, StarterPlayer settings, tags, attributes) |
| capture / record | host-side screenshot, opt-in / not supported |

## 4. Architecture

```
          Roblox Studio                                               autopilot serve
 ┌───────────────────────────┐   HTTP (long-poll, token)   ┌─────────────────────────────────────┐
 │ edit DataModel            │ ──── POST /v1/roblox/hello ─▶│ roblox_bridge.RobloxBridge          │
 │  Autopilot plugin (edit)  │ ◀─── exchange (frames) ─────▶│   one bridge.Link per session       │
 │  toolbar + dock: prompt   │                             │   routes each request to a channel: │
 │  session start, codex     │                             │     edit  : session, codex, status   │
 ├───────────────────────────┤                             │     play  : everything in the game   │
 │ play server DataModel     │ ──── POST /v1/roblox/attach ▶│     host  : capture (opt-in), record │
 │  Autopilot plugin (server)│ ◀─── exchange (frames) ─────▶│   synthesises 409 "No game is        │
 │  world ops, logs, EndTest │                             │   running" when no play channel      │
 │      │ RemoteFunction      │                             └─────────────────────────────────────┘
 │      ▼                     │                                   │ same bridge.Link interface
 │ play client DataModel     │                                   ▼
 │  AutopilotClientAgent     │                       cli / agents / kit / runtime (unchanged)
 │  (LocalScript): camera,   │
 │  input, GUI, pose         │
 └───────────────────────────┘
```

- **Why a second front door and not a different Link.** `bridge.Link` only needs a `send(frame)` callable and
  `feed(frame)` calls. The HTTP bridge gives it a `send` that queues into the right channel's outbox, and feeds what the
  plugin posts. `serve_local` starts it next to the WebSocket server (port 8766 by default, `AUTOPILOT_ROBLOX_PORT`;
  `0` turns it off) and hands its links out through the same `ready` queue, so `cli.py` and everything above it are
  unchanged.
- **Handshake.** Every request carries `X-Autopilot-Token`. The token is a random secret the bridge creates in
  `Autopilot/roblox_token` (user settings folder) or takes from `AUTOPILOT_ROBLOX_TOKEN`; `autopilot roblox install`
  bakes it into the plugin file it installs. A request without it gets 401 (a web page can't add that header to a
  request to localhost without a CORS preflight, which the bridge never grants). Then `hello` → `welcome` as on the
  WebSocket.
- **Endpoints** (`/v1/roblox/...`, JSON bodies, all POST but `ping`):
  - `GET ping` (no token): `{"ok": true, "protocol": 1, "service": "autopilot-roblox"}`, for `autopilot roblox status`.
  - `hello` (body: the hello frame): `{"type": "welcome", "session", "role", "features"}`. A hello with
    `resume: <session>` within 90 s gets the same session back.
  - `attach {session?, context: "server"}`: a play-side channel joins the session (the latest one when none is given).
  - `exchange {session, context, frames, wait}`: hands the plugin's frames to the link, then answers
    `{"frames": [...]}` with what is queued for this channel, waiting up to `wait` seconds (max 25) when there is none.
  - `bye {session, context}`: the channel ends. Requests routed to it and still unanswered are failed (409). The edit
    channel's bye closes the link.
  - A channel that hasn't polled for 60 s is dropped the same way.
- **Studio's own Play button.** A play test started by hand (not by Autopilot) has no test arguments; its server
  instance joins the session the dock is connected to (kept in the plugin's settings), and stays out of the way when
  the dock isn't connected. Without the client agent (only Autopilot's own starts put it in), camera, input and GUI
  requests answer 409.
- **Client ops.** The plugin's play-server instance answers world ops itself and forwards camera / input / GUI ops to
  the client agent through a `RemoteFunction` (`ReplicatedStorage.AutopilotAgentRemote`). The edit plugin inserts the
  agent `LocalScript` into `StarterPlayer.StarterPlayerScripts` and the remote into `ReplicatedStorage` just before it
  starts the play test (so they are copied into the play DataModels as ordinary objects) and removes them when the
  test ends. They are tagged `AutopilotTemporary`; if Studio crashes mid-run, delete them by hand before saving
  (the plugin also removes stale ones when it loads).

## 5. In scope / out of scope

In scope (implemented, UNVERIFIED against Studio): the HTTP bridge and session routing; play start/stop/restart; the
engine-neutral ops (`find`, `transforms`, `pose`, `movement`); `context`, `ping`, `rc` (property / attributes /
call / describe / probe), `screen`, `ui`, `ui_act` (`set` only), `view` (basic), `watch` (moved, turned, changed
size, part shown/hidden, gone), `key`, `input`, `move`, `detectors` (accepted, no thresholds), heartbeats, `log`
events and `log_error` findings (server and client); `codex_manifest` / `codex_read` (every script's text and a
summary of the place); a minimal dock (prompt box, Start, Stop, Steer, status line, result, address and token);
`autopilot roblox install|build|status|uninstall`; the codex from disk for a Rojo project or a `.rbxlx` place file
(`autopilot codex --project`).

Stubbed or answered "not possible": `capture` (409 unless `AUTOPILOT_ROBLOX_SCREENSHOT=1`, then a host-side
screenshot of the whole screen), `record` (409), `ui_act` clicks (501), `pause` and `timestep` (accepted, do nothing),
`session restart` with another `map` (400).

Out of scope for now: recording, GUI clicks, Team Test / multiple clients, `levels`, Aura sign-in and the first-run
setup UI the other plugins have, the run history and policy log tabs, `sonar` and the richer `view` fields (listed in
`omitted`), the `out_of_world` / `long_fall` / `hitch` detectors, a headless launch for the bench (Studio has no
headless play mode a plugin can drive), a release download (`tools/package_release.py` doesn't package Roblox).

## 6. Install

Requirements: Roblox Studio (macOS or Windows), Python 3.12+ with Autopilot installed from a clone (`pip install -e .`,
see CONTRIBUTING.md: the plugin's source is the clone's `plugin/roblox`), and your keys or Aura set up as for the
other engines.

1. **Build and install the plugin.**

   ```bash
   autopilot roblox install          # builds plugin/roblox into one Autopilot.server.lua and copies it into
                                     # Studio's Plugins folder, with this machine's bridge token baked in
   ```

   The Plugins folder is `~/Documents/Roblox/Plugins` on macOS and `%LOCALAPPDATA%\Roblox\Plugins` on Windows
   (`--plugins-dir` or `AUTOPILOT_ROBLOX_PLUGINS` overrides it). `--source` installs from another `plugin/roblox`
   folder or a prebuilt `Autopilot.server.lua` (its token placeholder is filled in); `--url` bakes in another bridge
   address. Without the CLI: `python plugin/roblox/build_plugin.py --out <Plugins>/Autopilot.server.lua` (no token:
   enter it in the dock, see below). With Rojo: `rojo build plugin/roblox/default.project.json -o
   <Plugins>/Autopilot.rbxm` (no token either). `autopilot roblox uninstall` removes the plugin file.
2. **Restart Studio** (or reload plugins) and open your place. An **Autopilot** button appears in the Plugins tab.
3. **Allow HTTP.** The first time the plugin calls the bridge, Studio asks whether Autopilot may make HTTP requests to
   `127.0.0.1`: allow it (Plugins > Manage Plugins > Autopilot > permissions, if you missed it). Also turn on
   **Game Settings > Security > Allow HTTP Requests** for the place: the play-server half of the plugin may need it
   (UNVERIFIED). Turning it on is a setting of the place: turn it off again before you publish if your game doesn't
   use HTTP itself.
4. **Start the agent service:** `autopilot serve`. It listens for Unreal/Unity/Godot on `ws://127.0.0.1:8765` and for
   Roblox on `http://127.0.0.1:8766` (`AUTOPILOT_ROBLOX_PORT`; `0` turns the Roblox bridge off). `autopilot roblox
   status` checks Studio, the installed plugin, its token and the bridge.
5. **Open the Autopilot dock** (the toolbar button). It connects by itself; the top line says "Connected to the agent
   service". If it says "Token rejected", paste the token `autopilot roblox status --show-token` prints into the dock's
   **Token** field and press **Save**.

Environment variables: `AUTOPILOT_ROBLOX_PORT` (bridge port, default 8766, 0 = off), `AUTOPILOT_ROBLOX_TOKEN` (use
this token instead of the one kept in `Autopilot/roblox_token`), `AUTOPILOT_ROBLOX_SCREENSHOT=1` (host-side
screenshots for `capture`), `AUTOPILOT_ROBLOX_PLUGINS` (the Plugins folder), `AUTOPILOT_ROBLOX_STUDIO` (where Studio
is, for `status`).

## 7. Use

Type a prompt in the dock and press **Start**: Autopilot starts a play test, reads the place's scripts, plays and
reports; the dock shows its progress lines and the result. **Stop** ends the run; **Steer** sends the prompt box's
words to the run while it plays. Prompts are one of the four kinds, as for every engine:

- **Exploration**: "Explore the first stage."
- **Testing**: "Go through the place and tell me everything that looks wrong."
- **Verification**: "Verify that touching a lava part kills the player."
- **Goal**: "Collect 5 coins without dying."

From the command line: `autopilot play "Verify that ..."` waits for the dock to connect and runs one prompt. From your
own Claude Code or Codex: `autopilot connect claude` (or `codex`), keep `autopilot serve` running with Studio open and
the dock connected, then ask your agent to "Use Autopilot to verify that ...".

## 8. Known limits and verification status

What was checked, offline only:

| Check | Result |
|---|---|
| `tests/test_roblox.py`: engine registration, project discovery, codex rules, `.rbxlx` scripts, kit frame, the kit section in the prompt, plugins folder and Studio detection, token, install / build / status / uninstall, the single-file build, session routing, host capture, channel timeouts, and a **fake plugin in Python driving the real HTTP bridge end to end** (hello, edit and server channels, requests, posts, events, start frames, restart orchestration, bye) | passing |
| The Luau: `stylua --check` (formatting and parse), `selene` (Roblox std) on `src/` and on the built single file, `rojo build` | clean |
| The plugin in Roblox Studio | **not run** (UNVERIFIED) |

Limits:

- **Nothing here has been run in Roblox Studio.** Every Studio behaviour this design leans on is UNVERIFIED:
  plugins running in the play server, `StudioTestService` arguments reaching it, `HttpService` from the play server to
  localhost, setting a new `LocalScript`'s `Source` from a plugin and it running in the play client,
  `ContextActionService:CallFunction` with no input object, `ProximityPrompt:InputHoldBegin` from a LocalScript, and
  the camera turning when its `CFrame` is set before the default camera script runs.
- Input is semantic, not real key events (see section 2). Games that read `UserInputService` directly for a key won't
  react to that key; movement through the default `PlayerModule` and `ContextActionService` bindings should.
- GUI clicks are not possible; `ui_act click` answers 501.
- No pictures unless `AUTOPILOT_ROBLOX_SCREENSHOT=1` (then the whole screen is captured, whatever is in front: macOS
  needs Screen Recording permission for the terminal that runs `autopilot serve`). No recording.
- `pause` and `timestep` do nothing.
- One player, Play mode (not Run, not Team Test).
- The client agent and its remotes are put in the place for the duration of a run (tagged `AutopilotTemporary`,
  removed when the test ends and when the plugin loads): saving the place mid-run saves them too, and Studio's undo
  history shows them come and go.
- The plugin polls at most 8 requests a second; each key press, answer and event batch rides on those, so a busy run
  adds up to ~125 ms of latency per round trip.
