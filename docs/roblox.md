# Autopilot for Roblox Studio

> **Status: experimental; the core is VERIFIED live** in Roblox Studio 0.745 on macOS (October 2026), in a throwaway
> Baseplate place: install, the handshake, Play start / stop / restart, reading the game and its scripts, moving and
> acting as the player, real keys and GUI clicks, pictures and recording, logs and errors, and runs of all four
> prompt kinds through `autopilot serve` and Claude Code (with the real Jev through Vercel AI Gateway, log round 6;
> earlier with a local stand-in: section 8).
> The log of every check, with the commands and their output, is [roblox-live-test-log.md](roblox-live-test-log.md);
> screenshots are in [roblox-live-evidence/](roblox-live-evidence/). What was not checked is still marked
> **UNVERIFIED** (Windows, Team Test, published places, games with their own input stacks).

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
| A plugin can't open sockets or listen. `HttpService:RequestAsync` is its only way out (HTTP/HTTPS, JSON in strings). (`HttpService:CreateWebStreamClient` was not tried.) | The brain gets a second, **HTTP long-poll** front door (`autopilot/roblox_bridge.py`) next to the WebSocket. The plugin polls it. |
| HTTP to `localhost` from a plugin. | VERIFIED: a plugin file in the Plugins folder reached `127.0.0.1` from the edit **and** the play-server DataModel with **no permission prompt** and with the place's **Allow HTTP Requests off** (`HttpEnabled = false`). Nothing to turn on. |
| Studio holds a play test's server to Roblox's HttpService budget, **about 500 requests a minute**, plugins included (VERIFIED: at 30 a second Studio refused requests within seconds). | The plugin spends one request per question (the poll that will carry an answer waits for it, then waits at the bridge for the next request), from a token bucket of 7 a second (420 a minute) with bursts of 40; it halves its rate if Studio ever refuses one. Measured: 17 ms a server answer, 67 ms a client one, 7 sustained questions a second (section 8). |
| Plugin `Script`s run once **per DataModel**: the edit DataModel and, during a play test, the play **server** (VERIFIED). Not in the play client. They can't share Lua state. | The edit instance is the "editor" (it says `hello`, owns the dock, starts play, serves the codex). The play-server instance attaches to the same session as a second channel and answers the game ops. The host routes each request to the right channel. |
| The client is reached through a script in the game. | VERIFIED: a `LocalScript` the **play server** creates at runtime and sets the `Source` of (with `ModuleScript` children) replicates **with its source** and runs in the client. So the play-server instance puts the client agent in each player's `PlayerGui` itself: the edit place is never touched, and Studio's own Play button gets it too. |
| No OS-level input from Luau: `VirtualInputManager` and `ContextActionService:CallFunction` are RobloxScript-only (VERIFIED: "lacking capability RobloxScript"). | Two layers. **Semantic** input in the client agent for movement and the camera (`Humanoid:Move`, `Humanoid.Jump`, the camera's `CFrame`, the equipped `Tool`, `ProximityPrompt:InputHoldBegin/End`: all VERIFIED). **Real** input through Roblox Studio's own MCP server (section 4, "Pictures and real input") for every other key, `ContextActionService` actions and GUI clicks (VERIFIED). |
| No screen capture from Luau (no API reads pixels). | `capture` and `record` are the host's: Roblox Studio's own MCP server's `screen_capture` gives the game viewport as a JPEG (VERIFIED: 0.3 s a picture, with Studio in the background). Fallback: `AUTOPILOT_ROBLOX_SCREENSHOT=screen` takes the whole screen. |
| GUI buttons can't be clicked from Luau (no `GuiButton:Activate()`; `Activated` can't be fired). | `ui_act` click: the client agent names the button (`LocalPlayer.PlayerGui.<...>`), the host clicks it for real through Studio's MCP server (VERIFIED: `Activated` fired). Without it: 501. |
| A plugin can't read the place's file path; an unpublished place has `PlaceId` 0. | `context.project_dir` is empty; the codex comes from the plugin's export (all scripts' `Source`), or from a Rojo project / `.rbxlx` on disk given with `--project`. |
| `StudioTestService:ExecutePlayModeAsync(args)` starts a play test from the edit DataModel and yields until it ends; `EndTest(result)` ends it from the play server; `GetTestArgs()` reads `args` there. | VERIFIED as described. `session start` → the edit plugin calls `ExecutePlayModeAsync({autopilot_session=...})`; the play-server plugin reads the session from `GetTestArgs()` and attaches; `session stop` is routed to the play server (`EndTest`); `restart` = stop, wait for the play channel to go, start (the edit plugin waits for `ExecutePlayModeAsync` to return first). |
| Running scripts can't be paused from Luau. | `pause` answers `{"paused": false, "note": ...}` (no-op), as Godot's `timestep` note does. |
| Studio doesn't load a plugin file added (or changed) while it runs. | After `autopilot roblox install`, restart Studio (VERIFIED: no hot reload of a `.lua` plugin file). |

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
| `ui` / `ui_act` | buttons and text boxes in `PlayerGui`; `ui_act` `set` on a TextBox; `click`: a real mouse click on the button through Studio's MCP server (else 501) |
| `key` | W A S D / arrows, SpaceBar, MouseX / MouseY, mouse buttons: the client agent (semantic, timed in game frames); every other key: a real key event through Studio's MCP server (held keys are leased or timed on the host), else the client agent's semantic fallback |
| `input` | a ProximityPrompt (by `ActionText` or name): the client agent; a ContextActionService action: its key, pressed for real |
| `move` | the client agent's `Humanoid:Move` for N seconds |
| `view` | parts / models in front of the camera (`Camera:WorldToViewportPoint`) with a line of sight (`Workspace:Raycast`) |
| `watch` | positions and `Transparency` sampled for N seconds on the server |
| logs / findings | `LogService.MessageOut` (server and, through the client agent, client): `log` events and `log_error` findings; what was logged before the plugin's play-server half or the client agent started (`LogService:GetLogHistory()`) is sent first |
| heartbeat, game time | play server, every second; `t` = `workspace.DistributedGameTime` |
| codex | every `LuaSourceContainer`'s `Source` as `scripts/<Service>/<path>.server.luau|.client.luau|.luau`, plus `summary.txt` (services' trees, RemoteEvents, StarterPlayer settings, tags, attributes) |
| capture / record | the host's, through Studio's MCP server `screen_capture` (record: a picture every 1/fps s, at most 4 a second) |

## 4. Architecture

```
          Roblox Studio                                               autopilot serve
 ┌───────────────────────────┐   HTTP (long-poll, token)   ┌──────────────────────────────────────┐
 │ edit DataModel            │ ──── POST /v1/roblox/hello ─▶│ roblox_bridge.RobloxBridge           │
 │  Autopilot plugin (edit)  │ ◀─── exchange (frames) ─────▶│   one bridge.Link per session        │
 │  toolbar + dock: prompt   │                             │   routes each request to a channel:  │
 │  session start, codex     │                             │     edit  : session, codex, status    │
 ├───────────────────────────┤                             │     play  : everything in the game    │
 │ play server DataModel     │ ──── POST /v1/roblox/attach ▶│     host  : capture, record, real     │
 │  Autopilot plugin (server)│ ◀─── exchange (frames) ─────▶│             keys, clicks, CAS actions │
 │  world ops, logs, EndTest │                             │   synthesises 409 "No game is         │
 │  puts the client agent in │                             │   running" when no play channel       │
 │      │ RemoteFunction      │                             └──────────────────────────────────────┘
 │      ▼                     │       stdio JSON-RPC                │            │ same bridge.Link interface
 │ play client DataModel     │ ◀── StudioMCP (Studio's own ───────┘            ▼
 │  AutopilotClientAgent     │     MCP server): screen_capture,      cli / agents / kit / runtime
 │  (LocalScript in PlayerGui│     user_keyboard_input,              (unchanged)
 │  ): camera, movement, GUI │     user_mouse_input
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
  the dock isn't connected. It puts the client agent in the game the same way (VERIFIED).
- **Client ops.** The plugin's play-server instance answers world ops itself and forwards camera / input / GUI ops to
  the client agent through a `RemoteFunction` (`ReplicatedStorage.AutopilotAgentRemote`). Both are made at runtime in
  the play test's DataModels: the remotes in `ReplicatedStorage`, the agent `LocalScript` (with the modules it needs)
  in a `ResetOnSpawn = false` ScreenGui named `AutopilotClientAgent` in each player's `PlayerGui`. The edit DataModel
  (the place you save) is never changed (VERIFIED after runs: nothing tagged `AutopilotTemporary`, nothing added).
  Earlier versions of the plugin put them in the edit place; the plugin still removes such leftovers when it loads.
- **The link's budget.** One request per question: when a poll brings requests, the next poll waits up to 0.25 s for
  their answers and carries them, then waits at the bridge (up to 2 s) for the next request. An answer that takes
  longer (a `move` of several seconds, a client op still running) goes in a request of its own; events (heartbeats,
  logs) ride the next poll, so they are at most 2 s late. Requests come from a token bucket, 7 a second on average.

### Pictures and real input: Roblox Studio's MCP server

Roblox Studio ships an MCP server, `StudioMCP` (`/Applications/RobloxStudio.app/Contents/MacOS/StudioMCP` on macOS,
`%LOCALAPPDATA%\Roblox\Versions\<version>\StudioMCP.exe` on Windows: UNVERIFIED there), which Studio's own AI
assistant tools use. It runs as a stdio JSON-RPC process that talks to the open Studio, and it can do what a plugin
can't. The bridge (`autopilot/studio_mcp.py`) starts one when it first needs it and keeps it:

- `screen_capture`: the game viewport as a JPEG (1292x648 here), in 0.3 s, also while Studio is behind other windows
  and with the picture live during Play. `capture` answers with it; `record` takes one every 1/fps s (at most 4 a
  second) and feeds them to the run as `frame`s, so runs get videos (`media/recording-N.mp4`) and the agent's frame
  describer gets pictures. The hello then says `media`.
- `user_keyboard_input` and `user_mouse_input` (`datamodel_type: Client`): real key and mouse events in the play
  client. ContextActionService bindings, UserInputService handlers, ProximityPrompts' own keys, the backpack's number
  keys and GUI buttons all see them.
- It only acts on the Studio instance whose place is the plugin's (`hello.project`, the place's name, or
  `<name>.rbxl`): never on another place that happens to be open. When Studio restarts (a new id), it finds it again.
- **No picture while a play test starts or stops.** A `screen_capture` in flight then can hang, and once left Studio's
  pictures broken until Studio restarted (VERIFIED, log round 5). The bridge takes none from a `session` start / stop
  / restart until 2 s after the play test's channel came or went; the recording skips those seconds. StudioMCP is
  never stopped in the middle of a call.

`AUTOPILOT_ROBLOX_STUDIO_MCP` names the executable (`0` turns it off). `AUTOPILOT_ROBLOX_SCREENSHOT` picks the
pictures: `studio` (the default), `screen` (a screenshot of the whole screen: macOS `screencapture`, Windows
PowerShell; it shows whatever is in front) or `0` (none: `capture` answers 409). `AUTOPILOT_ROBLOX_REAL_INPUT=0`
keeps all input semantic (and GUI clicks answer 501).

## 5. In scope / out of scope

In scope (VERIFIED live unless marked): the HTTP bridge and session routing; play start/stop/restart; the
engine-neutral ops (`find`, `transforms`, `pose`, `movement`); `context`, `ping`, `rc` (property read and write /
attributes / call / describe; `probe` UNVERIFIED), `screen`, `ui`, `ui_act` (`set` UNVERIFIED, `click` through
Studio's MCP server), `view`, `watch` (answers; its change detection UNVERIFIED), `key`, `input`, `move`, `detectors`
(accepted, no thresholds), heartbeats, `log` events and `log_error` findings (server and client, start-up errors
included); `codex_manifest` / `codex_read`; `capture` and `record` through Studio's MCP server; the dock (prompt box,
Start, Stop, Steer, status line, result: Start / Steer from the dock UNVERIFIED, runs were started from Claude Code);
`autopilot roblox install|build|status|uninstall`; the codex from disk for a Rojo project or a `.rbxlx` place file
(`autopilot codex --project`, UNVERIFIED live).

Answered "not possible": `pause` and `timestep` (accepted, do nothing), `session restart` with another `map` (400),
`ui_act` clicks and real keys without Studio's MCP server (501 / semantic only).

Out of scope for now: Team Test / multiple clients, `levels`, Aura sign-in and the first-run setup UI the other
plugins have (so the plugin says `share: false`: Roblox runs are never shared), the run history and policy log tabs,
`sonar` and the richer `view` fields (listed in `omitted`), the `out_of_world` / `long_fall` / `hitch` detectors, a
headless launch for the bench, a release download (`tools/package_release.py` doesn't package Roblox), the Roblox
Player (section 9).

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
2. **Restart Studio** (it doesn't load a new or changed plugin file while it runs) and open your place. An **Autopilot** button appears in the Plugins tab.
3. **HTTP needs nothing.** A plugin installed this way reached `127.0.0.1` without a permission prompt, with the
   place's **Allow HTTP Requests** off (VERIFIED on macOS). Should Studio ever ask whether Autopilot may make HTTP
   requests to `127.0.0.1`, allow that (Plugins > Manage Plugins > Autopilot > permissions, if you missed it).
4. **Start the agent service:** `autopilot serve`. It listens for Unreal/Unity/Godot on `ws://127.0.0.1:8765` and for
   Roblox on `http://127.0.0.1:8766` (`AUTOPILOT_ROBLOX_PORT`; `0` turns the Roblox bridge off). `autopilot roblox
   status` checks Studio, the installed plugin, its token and the bridge.
5. **Open the Autopilot dock** (the toolbar button). It connects by itself; the top line says "Connected to the agent
   service". If it says "Token rejected", paste the token `autopilot roblox status --show-token` prints into the dock's
   **Token** field and press **Save**.

Environment variables: `AUTOPILOT_ROBLOX_PORT` (bridge port, default 8766, 0 = off), `AUTOPILOT_ROBLOX_TOKEN` (use
this token instead of the one kept in `Autopilot/roblox_token`), `AUTOPILOT_ROBLOX_SCREENSHOT` (`studio`, the
default; `screen`; `0`), `AUTOPILOT_ROBLOX_STUDIO_MCP` (Studio's MCP server executable; `0` = don't use it),
`AUTOPILOT_ROBLOX_REAL_INPUT` (`0` = semantic input only), `AUTOPILOT_ROBLOX_PLUGINS` (the Plugins folder),
`AUTOPILOT_ROBLOX_STUDIO` (where Studio is, for `status`).

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

## 8. Verification status and known limits

Checked live on macOS 27, Roblox Studio 0.745.0.7451137, in a NEW unsaved Baseplate with the test game of
`tools/roblox_test_place.luau` (5 coins and leaderstats, a door that opens at 5 coins, a goal pad, a lever with a
ProximityPrompt, an F-key ContextActionService action, a Shop button, and two planted bugs). Details, commands and
outputs: [roblox-live-test-log.md](roblox-live-test-log.md).

| Check | Result | Evidence |
|---|---|---|
| Install, Autopilot toolbar button and dock, handshake | VERIFIED (after a Studio restart) | [01](roblox-live-evidence/01-toolbar-button.png), [02](roblox-live-evidence/02-dock-connected.png) |
| Play start / stop / restart from Autopilot; Studio's own Play joins | VERIFIED | [03](roblox-live-evidence/03-play-started-by-autopilot.png), log rounds 1-2 |
| State: `context`, `pose`, `find`, `movement`, `rc`, `view`, `screen`, `ui`, codex (the place's scripts) | VERIFIED | log rounds 1-2 |
| Acting: `move`, held keys, jump, camera, ProximityPrompt, real keys, CAS action by its key, GUI click | VERIFIED | log round 2 |
| Pictures: `capture` and `record` (videos of each episode) through Studio's MCP server | VERIFIED | [04](roblox-live-evidence/04-capture-during-play.jpg), [05](roblox-live-evidence/05-e2e-verify-recording-frame.jpg) |
| Logs and `log_error` findings, server and client, start-up errors | VERIFIED | log rounds 1-3 |
| Latency within Studio's HttpService budget | 17 ms server / 67 ms client answers, 7 a second sustained, no refusals | log round 3 |
| The edit place untouched by runs | VERIFIED | log round 2 |
| The four prompt kinds through `autopilot serve` + Claude Code (Autopilot's MCP server, skill and describer) | Verification CONFIRMED (twice), Goal achieved, Testing and Exploration explored with real bugs found; every run reported | log round 4, [e2e-*](roblox-live-evidence/), [07](roblox-live-evidence/07-e2e-goal-reached.jpg), [10](roblox-live-evidence/10-dock-final-verdict.png) |
| Pictures across Studio restarts and play restarts; a play test rejoining after the agent service restarts | VERIFIED after fixes | log round 5 |
| The four prompt kinds with the real Jev through Vercel AI Gateway; Autopilot's own Claude Code agent and describe_frames through it | all four reached their verdict; internal agent CONFIRMED; frames described correctly | log round 6, [gateway/](roblox-live-evidence/gateway/) |
| `tests/test_roblox.py`, the full suite, `stylua`, `selene`, `rojo build` | passing / clean | |
| Windows, Team Test, a published place, Rojo-synced places | UNVERIFIED | |

**The decision model.** A run's episodes are played by Jev, TypeSafe's hosted decision model, reached through Vercel
AI Gateway when there is a gateway key (the default: [gateway.md](gateway.md)), else with `TYPESAFE_API_KEY` or
through Aura. Rounds 4-5 used a local stand-in (`AUTOPILOT_JEV_STANDIN=random`, `runtime.StandInJev`, random picks);
**round 6 replayed all four prompt kinds with the real Jev (`jev-1.13.0`) through the gateway**: Verification
CONFIRMED, Goal achieved, Testing and Exploration explored, in fewer episodes than with the stand-in, Testing finding
the planted Coin3 bug too; decisions answered in 0.18 s (p50) / 0.27 s (p90), no errors or timeouts, Jev's cost about
$0.013 for all four. Autopilot's own Claude Code agent and the vision model were also checked through the gateway. `autopilot connect claude` itself wasn't run (it writes the
user-wide Claude Code config of a machine shared by other agents); the runs loaded the same files it installs
(`python -m autopilot.connect plugin <folder>`, which the tests compare with what `connect` writes) with
`claude --plugin-dir`.

Limits:

- Input is semantic for movement and the camera; other keys and GUI clicks need Studio's MCP server (on by default
  where Studio ships it). A game that reads raw `UserInputService` for **movement** keys won't see the agent walk.
- Studio's HttpService budget caps the link at about 7 questions a second, sustained (bursts are faster).
- `pause` and `timestep` do nothing; one player, Play mode (not Run, not Team Test).
- Studio's MCP server acts on the Studio whose place has the plugin's place name: with two open places of the same
  name, the first one listed.
- Recordings skip about 2 s around each play test start and stop (see "Pictures and real input").
- If Studio's pictures ever stop answering (`capture` 409, "did not answer"), restart Studio.
- The dock shows a run's verdict and its reason or notes; it is small, so a long result needs scrolling.

## 9. The Roblox Player (not Studio)

Assessed, not built. Plugins run only in Studio; the Roblox Player runs none of this. What a Player version would
need, and why none of it is trivial:

- **No way in from the game.** A live game server can't reach the player's computer (`HttpService` calls go out from
  Roblox's servers, never to the player's `localhost`), and a client script can't make HTTP requests at all. So the
  game's state could only be read through something the developer builds into the game itself (e.g. a debug
  `RemoteFunction` and an in-game panel), not by an add-on.
- **Input and pictures from the operating system only.** Real key and mouse events to the Player window (macOS
  `CGEvent` / System Events with Accessibility permission; Windows `SendInput`), and screenshots of its window. The
  agent would play from pictures alone: no `pose`, `find`, `rc`, codex or logs. That is a different, vision-only
  product shape.
- **The place running in the Player is a published one** (or a Team Test server), so testing there means publishing
  first; Studio's play test needs neither.

Recommendation: keep Studio as the target (its play test is the same engine and the same client code). For a check in
the real client, a developer-built debug channel plus OS-level input and window capture would be the way; it should
be designed separately.
