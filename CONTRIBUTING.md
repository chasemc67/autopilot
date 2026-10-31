# Contributing to Autopilot

Thank you for your interest in improving Autopilot. This guide explains how to set up a development environment, run
Autopilot from a clone, what the tests cover, and how changes are reviewed and released.

## Ways to help

- **Report a bug.** Open an issue with what you ran, what you expected, and what happened. For a run that went
  wrong, `memory/<project>/runs/<run>/` holds the agent's log, the harness versions, the traces' summaries and the
  report: attach what you can share (it may quote your game's code).
- **Suggest a feature.** Open an issue describing the problem you want solved before writing a large change, so we
  can agree on the approach.
- **Send a pull request.** Small, focused pull requests are reviewed fastest.

## Requirements

- Python 3.12+, pip, and [uv](https://docs.astral.sh/uv/)
- [Bun](https://bun.sh)
- A project in Unreal 5 (5.4-5.8), Unity 6, or Godot 4 (4.7 or later)

## Development setup

```bash
git clone <your fork> autopilot && cd autopilot
uv venv
uv pip install -e ".[dev]"        # Autopilot, Claude Code's SDK, pytest and ruff
cp .env.example .env              # only needed to run real prompts; the tests need no keys
```

Run the checks the CI runs:

```bash
ruff check .                      # lint (ruff format . formats)
pytest                            # the Python tests: no game engine, no keys, no network
```

The Python tests use a simulated game (`tests/conftest.py`, `tests/navsim.py`), so they run anywhere in a couple of minutes. Tests should not require a model, a key or a real engine.

## Running from a clone

1. **Add the plugin to a game:**

   | Engine   | Plugin                                                                             | Install                                                               |
   | -------- | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
   | Unreal 5 | [plugin/unreal/Autopilot](plugin/unreal/Autopilot/README.md)                       | copy the folder into your project's `Plugins/` and reopen the project |
   | Unity 6  | [plugin/unity/com.ramenvr.autopilot](plugin/unity/com.ramenvr.autopilot/README.md) | Package Manager > _Add package from disk_ > its `package.json`        |
   | Godot 4  | [plugin/godot](plugin/godot/README.md)                                             | copy `addons/autopilot` into your project and enable it               |
   | Roblox Studio (experimental, UNVERIFIED) | [plugin/roblox](plugin/roblox/README.md)          | `autopilot roblox install` ([docs/roblox.md](docs/roblox.md))         |

2. **Start Autopilot:** `autopilot serve` (or `python -m autopilot serve`). It waits for the plugin on
   `ws://127.0.0.1:8765/v1/bridge`.
3. **Open Autopilot** in your editor (Unreal: the orange Autopilot icon; Unity: Window > Autopilot; Godot: the
   Autopilot dock) and go through the first-run setup, as in the [README](README.md#installing-pre-built-packages)'s
   step 3.
4. **Prompt Autopilot.** A prompt is one of four types (see the [README](README.md#usage)): an exploration ("Explore
   the crafting system in Lvl_Main"), a test ("Go through the currently open level and tell me everything that looks
   wrong"), a verification ("Verify that the tree shakes when you hit it with an axe") or a goal ("Kill the boss while
   keeping your health above 20% of its maximum value"). Autopilot starts the game, reads the project's code, tries
   things in the game and plays; then the game closes and the report opens.

The first prompt on a new game takes about 5–15 minutes: the agent reads the code, tries things, and plays a few
episodes. Later prompts reuse what it learned. To run Autopilot from your own Claude Code or Codex instead, run
`autopilot connect claude` or `autopilot connect codex` (or, for Claude Code,
`claude plugin install autopilot --marketplace ramenvr/autopilot`), then ask your agent to "Use Autopilot to ...".

To build the Unreal plugin for an engine built from source, in one command on Windows (uv and its Python come by
themselves; a clone needs Git, GitHub's **Download ZIP** works too):

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_unreal.ps1 `
  -EngineDir "C:\path\to\UnrealEngine" `
  -PluginsDir "C:\path\to\YourProject\Plugins"
```

It builds Autopilot for that engine and installs it in the project as `Plugins\Autopilot\` (close the project's editor
first). Without `-PluginsDir` it only builds: copy `dist\Autopilot\` into `Plugins\` yourself. `-AuraServer` is optional
(default: Aura's own server); `dist\` also gets the zip, to share with others on the same engine build.

## Command line

```bash
autopilot serve                              # run what the editor's Autopilot tab starts
autopilot play "Verify that ..."             # run one prompt when the plugin connects, exit with its result
autopilot codex --project path/to/Game       # build a project's codex without the game running
autopilot run memory/<project>/runs/<run>/harness.py --seconds 90   # replay a saved harness (no language model)
autopilot login | logout                     # sign in to Aura on its website (without the Aura app), or out again
autopilot connect claude|codex [--remove]    # set up your own Claude Code or Codex to playtest through Autopilot,
                                             # on your own plan (its MCP server, `autopilot mcp`; a skill; a describer)
autopilot mcp                                # that MCP server (your agent starts it)
autopilot uninstall [--keep-memory]          # remove Autopilot from this computer and its memories, here and with Aura
                                             # (--keep-memory keeps them; shared run data already sent stays)
```

Exit codes: **0** confirmed or achieved, **2** denied, **3** inconclusive, **1** not achieved or an error. The plugins can
also start a run from the game's own command line (headless) for CI: each plugin's README lists its arguments.

## Configuration

Settings are environment variables. Autopilot reads them from three places:

|     | Where                                                                                                                                                                                                              | Who writes it                                                                  | When it is read                                          |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------ | -------------------------------------------------------- |
| 1   | The environment: your shell, CI, or the program that starts Autopilot (the Aura app sets `AUTOPILOT_AURA_URL` and `AUTOPILOT_MEMORY` for the Autopilot it runs)                                                    | you, or that program                                                           | at start                                                 |
| 2   | `.env` in the repository, from a clone only (`cp .env.example .env`)                                                                                                                                               | you                                                                            | at start; a variable the environment already has is kept |
| 3   | `autopilot.env` in the user settings folder: `%LOCALAPPDATA%\Autopilot\` on Windows, `~/Library/Application Support/Autopilot/` on macOS, `$XDG_CONFIG_HOME/Autopilot/` (default `~/.config/Autopilot/`) elsewhere | the plugins' first-run setup and the downloads' installer; you may edit it too | at start and again before every run, over 1 and 2        |

An env variable in `autopilot.env` overrides an env variable with the same key in the repo's `.env`. `autopilot.env` is shared by every editor and every Autopilot on the computer. On the other hand, `.env` belongs to one clone (each worktree has its own). An empty value counts as none.

What goes where:

- **The first-run setup's keys** go into `autopilot.env`: **Use my keys** writes `TYPESAFE_API_KEY`, `AUTOPILOT_AGENT`,
  `AUTOPILOT_OPENCODE_PROVIDER` (OpenCode only) and the provider's key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
  `OPENCODE_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY` or `GEMINI_API_KEY`). It replaces those lines and leaves
  the rest of the file alone.
- **A download's settings** go into `autopilot.env` too: its installer writes what the download carries, such as
  `AUTOPILOT_AURA_SERVER` for a download built with `--aura-server`. A later download takes out what an earlier one
  wrote, unless you changed it since.
- **A clone's settings** go into `.env`: your keys for the command line and the bench, and anything you set while
  developing (`AUTOPILOT_AURA_SERVER` for a staging or preview Aura, `AUTOPILOT_MEMORY`, `AUTOPILOT_SANDBOX`, ...).
  If `autopilot.env` sets the same variable (because a setup or a download ran on this computer), its value wins:
  take that line out of `autopilot.env` for `.env`'s to count.
- **One-off and CI settings** go in the environment: `AUTOPILOT_SHARE=0 autopilot play ...`.

Two choices are not environment variables: how runs go (through Aura or with your own keys) and whether runs' data is
shared are in `preferences.json` beside `autopilot.env`, written by the first-run setup (`--aura` or
`AUTOPILOT_AURA_URL` at start overrides the first). The plugins' own settings (Agent service command, Agent Service
URL, Use with Aura) are editor settings, kept per project; their READMEs list them.

The variables:

| Variable                                                 | Default                                                                                                         | What it does                                                                                                                                                                                                                 |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TYPESAFE_API_KEY`                                       | (required without Aura)                                                                                         | Jev's API key                                                                                                                                                                                                                |
| `AUTOPILOT_JEV_MODEL`                                    | `jev-1.13.0`                                                                                                    | Jev's version (an exact id: through Aura, only one Aura has a price for)                                                                                                                                                     |
| `ANTHROPIC_API_KEY`                                      | (required by default, without Aura)                                                                             | the agent's language model key                                                                                                                                                                                               |
| `AUTOPILOT_AURA_URL`                                     | (unset: your own keys)                                                                                          | run through Aura at this server (what `--aura` sets: `https://app.tryaura.dev`)                                                                                                                                              |
| `AUTOPILOT_AURA_SERVER`                                  | `https://app.tryaura.dev`                                                                                       | the Aura server used when Aura is chosen (`--aura`, the first-run setup) and for shared run data, without choosing Aura itself: e.g. a staging or preview server                                                             |
| `ANTHROPIC_BASE_URL`                                     | (the provider's own)                                                                                            | the model API's root, for a proxy or gateway in front of it; with OpenCode, any provider's `<PROVIDER>_BASE_URL` (e.g. `OPENAI_BASE_URL`)                                                                                    |
| `AUTOPILOT_AGENT`                                        | `opencode`                                                                                                      | `opencode` (OpenCode), `claude` (Claude Code) or `codex` (Codex)                                                                                                                                                             |
| `AUTOPILOT_MODEL`                                        | the provider's: `claude-opus-5-5`, OpenAI's `gpt-6.1-sol` (the rest: `MODELS` in `autopilot/agents/session.py`) | the agent's language model                                                                                                                                                                                                   |
| `AUTOPILOT_EFFORT`                                       | `medium`                                                                                                        | how hard the model thinks per turn                                                                                                                                                                                           |
| `AUTOPILOT_TIMEOUT`                                      | `1800`                                                                                                          | one prompt's whole budget, in seconds, when the run doesn't give one (the plugins' Settings page sets it for verify and goal runs)                                                                                           |
| `AUTOPILOT_OPENCODE_PROVIDER`                            | `anthropic`                                                                                                     | OpenCode's provider (one of [OpenCode's](https://opencode.ai/v2/docs/providers/); the setup offers `anthropic`, `openai`, `opencode` (OpenCode Zen), `openrouter`, `deepseek` and `google` (Gemini); each reads its own key) |
| `AUTOPILOT_VISION_MODEL`                                 | `claude-opus-5-5` (Anthropic, OpenCode Zen, OpenRouter), else the agent's model                                 | the model that describes the game's frames when the agent asks how something looks (none on DeepSeek)                                                                                                                        |
| `AUTOPILOT_BUN`                                          | (found)                                                                                                         | the Bun executable, when it isn't on `PATH` or in `~/.bun/bin`                                                                                                                                                               |
| `AUTOPILOT_FAST`                                         | `1`                                                                                                             | OpenCode and Claude Code: fast mode for Opus models (~2x faster output at twice the price per token; `0` turns it off)                                                                                                       |
| `AUTOPILOT_TURNS`                                        | `120`                                                                                                           | Claude Code only: the most turns per prompt                                                                                                                                                                                  |
| `AUTOPILOT_SANDBOX`                                      | `auto`                                                                                                          | where model-written code runs: `auto`, `bwrap`, `process` or `off`                                                                                                                                                           |
| `AUTOPILOT_MEMORY`                                       | `memory/` in the repository (installed: `Autopilot/memory` in your user settings folder)                        | where Autopilot keeps what it learns about each game                                                                                                                                                                         |
| `AUTOPILOT_SHARE`                                        | `1`                                                                                                             | `0`: never share runs' data, whatever the plugin says                                                                                                                                                                        |
| `AUTOPILOT_TRAINING_URL`                                 | Aura's                                                                                                          | where shared runs' data is sent                                                                                                                                                                                              |
| `AUTOPILOT_INSPECT`                                      | (off)                                                                                                           | `1` sends each decision to an editor's policy log even when the editor is on another machine                                                                                                                                 |
| `AUTOPILOT_UNREAL`, `AUTOPILOT_UNITY`, `AUTOPILOT_GODOT` | the usual install places                                                                                        | the engine to run headless games and exports with                                                                                                                                                                            |
| `AUTOPILOT_ROBLOX_PORT`, `AUTOPILOT_ROBLOX_TOKEN`, `AUTOPILOT_ROBLOX_SCREENSHOT` | `8766`, the one in `Autopilot/roblox_token`, off                                          | the Roblox Studio bridge's port (`0`: none), its token, and host-side screenshots for `capture` ([docs/roblox.md](docs/roblox.md))                                                                                          |

## Repository layout

| Path                                                                                           | What it is                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ---------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `autopilot/`                                                                                   | the Python package                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `autopilot/cli.py`                                                                             | the command line (`autopilot serve`, `play`, `codex`, `run`, `login`, ...)                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `autopilot/bridge.py`                                                                          | the WebSocket link to the plugin (its protocol: `PROTOCOL`, the messages it sends and reads)                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `autopilot/roblox_bridge.py`, `autopilot/roblox_tools.py`                                      | the Roblox Studio plugin's HTTP long-poll front door (its sessions are `bridge.Link`s), and `autopilot roblox install\|build\|status\|uninstall` ([docs/roblox.md](docs/roblox.md))                                                                                                                                                                                                                                                                                                                                       |
| `autopilot/agents/`                                                                            | the agents: `session.py` (instructions and game tools they share), `opencode.py` (with `opencode_host/`), `claude_code.py`, `codex.py` (with `codex_tools.py`, and `files.py`: its file tools), `external.py` (your own Claude Code or Codex); `toolserver.py`, the game tools over localhost; `feed.py`, the activity the plugins' Feed tab shows                                                                                                                                                                        |
| `autopilot/mcp.py`, `autopilot/connect.py`, `autopilot/assistants/`                            | `autopilot mcp`, `autopilot connect` and the skill it installs                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `autopilot/vision.py`                                                                          | describes the game's frames for a request about how something looks                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `autopilot/runtime.py`                                                                         | plays a harness: asks Jev, runs the chosen action, writes the trace                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `autopilot/kit/`                                                                               | what harnesses are written with: game reads and inputs, controllers (`walk_to`, `look_at`, ...), the `Harness` contract                                                                                                                                                                                                                                                                                                                                                                                                   |
| `autopilot/KIT.md`                                                                             | the kit's reference, as the agent reads it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `autopilot/codex.py`                                                                           | builds a project's codex (its code and assets as text)                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `autopilot/engines/`                                                                           | one adapter per engine: project files, coordinate frames, headless launch                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `autopilot/sandbox.py`                                                                         | runs model-written code in a separate process                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `autopilot/words.py`                                                                           | the words the user reads: internal names rewritten in plain words (`plain_words`)                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `autopilot/telemetry.py`, `autopilot/media.py`, `autopilot/training.py`, `autopilot/redact.py` | a run's usage and data: model calls, recording and stills, shared once redacted                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `autopilot/aura.py`, `autopilot/aura_login.py`, `autopilot/aura_memory.py`                     | runs through Aura, its sign-in and the memory kept with it                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `autopilot/uninstall.py`                                                                       | `autopilot uninstall`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `plugin/`                                                                                      | the Unreal (`unreal/Autopilot`), Unity (`unity/com.ramenvr.autopilot`), Godot (`godot/addons/autopilot`) and Roblox Studio (`roblox`, experimental) plugins, each with a README giving its source map and how to build and test it; and `claude-code/`: Autopilot's Claude Code plugin (listed in `.claude-plugin/marketplace.json`)                                                                                                                                                                                                                              |
| `packaging/standalone/`                                                                        | the downloads' installers and README                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `bench/`                                                                                       | runs a suite of prompts against a headless copy of a game and scores it                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `tools/`                                                                                       | `package_release.py`, which makes the downloads, `release.py`, which publishes them as a GitHub Release, and `check_download.py`, `engine_smoke.py`, `uninstall_e2e.py`, which try them; `build_unreal.ps1`, the Unreal plugin for any engine build; `fresh_setup.py`, to see the first-run setup again; `probe.py` and `rc.py`, for looking into a running game by hand; `net_smoke.py`, a multiplayer Unreal game; `training_data.py`, which reads runs' data; `relay_latency.py`, what Aura's relay adds to a decision |
| `tests/`                                                                                       | the Python tests (no engine needed): `pytest`                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |

`autopilot/bridge.py` and the plugins' link code are the contract between the plugins and Autopilot. A change to a
message or a request must keep working with all three plugins.

## Coding guidelines

- **Match the code around you**: its naming, comment density and idiom. Python follows ruff's rules at a line length
  of 120 (see `pyproject.toml`).
- **Comments explain why**, in plain English. Docstrings say what a function returns and what its arguments mean.
- **Keep the brain game-agnostic.** Nothing in `autopilot/` (code, prompts, examples, `KIT.md`) may name a specific game,
  its objects or its numbers. Illustrate a rule with an invented game. Autopilot must learn each game from its code at run
  time.
- **Keep the user-facing words consistent.** Use terms like _agent_, _policy_, _action_, _observation_. `autopilot/words.py` (`plain_words`) rewrites internal names in reports.
- **Add a test** for a bug fix or a new behavior, in `tests/` for Python. Engine-plugin changes are tested by the
  plugin's own tests (see its README).
- **Don't break the protocol.** The protocol is versioned (`protocol: 1`); add optional fields rather than changing
  existing ones.

## Changing the agent's instructions

The agent's instructions (`autopilot/agents/session.py`, `SYSTEM`) and the kit's reference (`autopilot/KIT.md`) decide how
well Autopilot plays. Changes to them are judged by results: describe in the pull request which games and prompts you ran
before and after, and how the outcomes changed. `bench/run_autopilot.py` runs a suite of prompts against a headless copy of
a game. Report results on games you didn't tune the change on.

## Adding an engine

Supporting another engine takes two parts:

1. **A plugin** that speaks the protocol (`autopilot/bridge.py`): it connects out to Autopilot, answers the requests
   (the engine-neutral ops and the kit frame the other plugins answer), sends events, and exports the project's code.
   The Unity and Godot plugins are the best models.
2. **An adapter** in `autopilot/engines/`: a subclass of `OpEngine` (`autopilot/engines/base.py`) that knows the project's
   layout, which files go into the codex, how to convert coordinates, how to launch the game headless, and the
   engine's section of the kit reference (`<engine>.md`). Register it in `autopilot/engines/__init__.py` and add tests
   like `tests/test_godot.py`.

## Adding an agent

An agent is a module in `autopilot/agents/` with an `implement(game, project, prompt, ...)` function that returns the
report. Use `session.open_session`, `session.first_message` and `session.finish` so it shares the instructions,
briefing and tools with the others (`session.TOOL_SPECS` has the tools' names, descriptions and input schemas), then
add it to `autopilot/agents/__init__.py`. An agent that runs in another process reaches the game tools over localhost
through `toolserver.ToolServer`, as OpenCode's and Codex's do. Test it with a stand-in for the agent, as
`tests/test_opencode.py` does: no model, key or agent program in the tests.

The OpenCode agent's host (`autopilot/agents/opencode_host/`) pins its packages: to move to a new OpenCode version,
change the versions in its `package.json`, run `bun install` there and commit `package.json` and `bun.lock` together.

## Continuous integration

Every pull request runs (`.github/workflows/`):

- **CI**: `ruff check .` and `pytest` on Linux, Windows and macOS with Python 3.12 and 3.13, and `tests/test_codex.py` with
  the `codex` extra installed (a whole Codex run against a stand-in for OpenAI's API).
- **Commit Lint**: the pull request's title must be a [conventional commit](https://www.conventionalcommits.org/)
  (`<type>(<scope>): <description>`, e.g. `fix(unity): ...`, `feat(plugins): ...`).
- **Download**, when the brain, the packaging or the Godot or Unity plugin changes: packages the Godot and Unity downloads
  and installs and plays one, on Windows and on macOS (Apple Silicon and Intel), on computers that have never had
  Autopilot (`tools/check_download.py`).
- **Uninstall e2e**, when the brain, the packaging or the Godot plugin changes: _Remove from this computer_ end to end on
  Linux, macOS and Windows (`tools/uninstall_e2e.py`).

A merge to main that changes the brain, its dependencies or the Unreal plugin also asks Aura's repository to sync
(`aura-sync.yml`).

## Pull requests

1. Create a branch from `main`.
2. Make your change, with tests and documentation.
3. Run `ruff check .` and `pytest`.
4. Open the pull request with a conventional-commit title and a description of the problem and the change. Link the
   issue if there is one.

Every pull request to `main` needs a code owner's approval (`.github/CODEOWNERS`) and the checks above to pass.

By contributing, you agree that your contributions are licensed under the [Apache License 2.0](LICENSE).
