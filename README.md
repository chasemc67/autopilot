# Aura Autopilot

> By the developers of Aura, [www.tryaura.dev](https://www.tryaura.dev/)

![Aura by Ramen](./assets/aura-logo.png)

Autopilot is a continually learning real-time general playtesting system. It supports Unreal Engine 5, Unity 6, and Godot 4 on both Windows and MacOS, and (experimental, see [Roblox Studio](#roblox-studio-experimental)) Roblox Studio.

Install Autopilot into your project and let it learn your game, explore your levels, and improve its gameplay run after run.

## Installation

You can find the installation instructions in the [Autopilot docs](https://www.tryaura.dev/documentation/autopilot/)

### Installing pre-built packages

1. Download the latest Autopilot build for your game engine and OS from https://www.tryaura.dev/autopilot/.

2. Install Autopilot into your project.

   **Unreal** (v5.4-5.8)
   1. Unzip the download. Move `Autopilot/` into your project's `Plugins/` folder (create a `Plugins/` folder yourself if your project doesn't have one yet). You should therefore have `<YourProject>/Plugins/Autopilot/Autopilot.uplugin`.
   2. Open your project. Click the orange Autopilot icon to open Autopilot.

   **Unity** (v6 or later)
   1. Unzip the download. It should contain the file `com.ramenvr.autopilot-<version>.tgz`.
   2. Open your project. In Window > Package Management > Package Manager, choose + > Install package from tarball..., and pick that file.
   3. Open Window > Autopilot.

   **Godot** (v4.7 or later)
   1. Unzip the download. Move `autopilot/` into your project's `addons/` folder.
   2. Open your project. Enable Autopilot in Project > Project Settings > Plugins. The Autopilot dock appears on the right.

3. Follow the Autopilot setup flow.
   1. We recommend using Autopilot with your Aura account. Click `Create Account` if you don't yet have an Aura account, or `Sign in` if you are a returning user.
   2. Autopilot runs its setup, and finally redirects you to the signup/signin page. Complete your signin/signup process.
   3. Upon successful authentication, you should see the "Agent service ready" page. Click Continue.
   4. You have the option to enable/disable sharing your runs to improve Autopilot. As the developers of Autopilot, we would of course appreciate your enabling this!
   5. (Optional) Run the 10-minute exploration run to populate your Autopilot project memory. It is safe to skip this and jump straight to sending your own prompt.
   6. Prompt Autopilot! See the types of prompts you can try in [Usage](./README.md#usage).

### Installing from source

You can install Autopilot from source. This is supported for Unreal Engine only. This method allows you to build Autopilot against an Unreal source build, and places the Autopilot plugin into your Unreal project's Plugins folder automatically.

```powershell
git clone https://github.com/ramenvr/autopilot.git
cd autopilot
powershell -ExecutionPolicy Bypass -File tools\build_unreal.ps1 `
  -EngineDir "C:\path\to\UnrealEngine" `
  -PluginsDir "C:\path\to\YourProject\Plugins"
```

Once that has completed, follow the Autopilot setup flow as shown in Step 3 of [Installing pre-built packages](./README.md#installing-pre-built-packages).

## Usage

The main way to use Autopilot is through the **Autopilot plugin** in your editor. This works in Unreal Engine 5, Unity 6, and Godot 4 on both Windows and MacOS. This path uses your Aura plan, or your BYOK API keys.

Alternative ways to use Autopilot include:

- **In Aura**: You can also use Autopilot in Aura. Only Unreal and Unity are supported through this path. This path uses your Aura plan.
- **In Claude Code / Codex**: You can use Autopilot through Claude Code and Codex via Autopilot's skills and MCP server. To set this up, run `autopilot connect claude` and/or `autopilot connect codex` in your terminal, then prompt Claude Code / Codex to "Use Autopilot to ...". This path uses your own Claude/ChatGPT subscription.

An Autopilot prompt is one of 4 types:

- **Exploration**, e.g. "Explore the crafting system in Lvl_Main."
- **Testing**, e.g. "Go through the currently open level and tell me everything that looks wrong."
- **Verification**, e.g. "Verify that the tree shakes when you hit it with an axe."
- **Goal**, "Kill the boss while keeping your health above 20% of its maximum value."

For every prompt, Autopilot will automatically launch a PIE window and start reading your project's source code, probe PIE, and start playing according to your prompt. Autopilot plays by using an LLM coding agent to craft harnesses for a decision model according to what it knows about your game, and requests decisions (action choices) from the decision model during gameplay. Once Autopilot is done, the PIE window will close automatically, and Autopilot generates a report for you.

Your first few runs allow Autopilot to learn about your game as it gradually populates its memory. Expect some suboptimal behavior for the first few harness attempts. **Autopilot visibly improves the more it plays your game**, and later harness attempts and runs both take a shorter time to complete a given task, as well as exhibit more intelligent behavior (e.g. faster dodging, equipping better weapons, playing smarter strategies, better aiming).

## Roblox Studio (experimental)

> **UNVERIFIED.** Roblox Studio support was written and tested offline only (unit tests, a fake plugin against the real
> bridge, selene/stylua/Rojo on the Luau). It has not been run in Roblox Studio yet. The full design, install steps
> and limits are in [docs/roblox.md](docs/roblox.md).

A Roblox Studio plugin can't open a WebSocket, so the plugin (`plugin/roblox`) long-polls a small HTTP bridge that
`autopilot serve` runs next to its WebSocket (`http://127.0.0.1:8766/v1/roblox`, guarded by a token).

**Install** (from a clone, with Autopilot installed: see [CONTRIBUTING.md](CONTRIBUTING.md#development-setup)):

1. `autopilot roblox install`: builds the plugin into one `Autopilot.server.lua` with this computer's token and copies
   it into Studio's Plugins folder (`~/Documents/Roblox/Plugins` on macOS, `%LOCALAPPDATA%\Roblox\Plugins` on
   Windows).
2. Restart Studio and open your place. Allow the plugin's HTTP requests to `127.0.0.1` when Studio asks, and turn on
   **Game Settings > Security > Allow HTTP Requests**.
3. Run `autopilot serve`, then open the **Autopilot** dock from the Plugins tab. `autopilot roblox status` checks the
   setup.

**Use**: type a prompt in the dock and press **Start** (the same four prompt types as below), or connect your own
Claude Code / Codex with `autopilot connect claude|codex`.

**Known limits**: input is semantic (a plugin can't make real key events: movement keys walk the Humanoid, Space jumps,
the mouse turns the camera, keys trigger ProximityPrompts, Tools and ContextActionService actions; keys a game reads
only through `UserInputService` do nothing); GUI buttons can't be clicked; no recording, and screenshots only with
`AUTOPILOT_ROBLOX_SCREENSHOT=1` (a screenshot of the whole screen); `pause` does nothing; one player in Play mode.

## Uninstallation

1. In Autopilot, go to Settings and click Uninstall. By default, Autopilot keeps your project's memories; you can choose to delete memories instead (this is a destructive action).
2. Remove the plugin from each project: Unreal, close the editor and delete `Plugins/Autopilot/`; Unity, remove the Autopilot package in the Package Manager; Godot, disable Autopilot in Project > Project Settings > Plugins, then delete `addons/autopilot/`; Roblox Studio, run `autopilot roblox uninstall` (or delete `Autopilot.server.lua` from Studio's Plugins folder).

---

## Contributing

Contributions are welcome. [CONTRIBUTING.md](CONTRIBUTING.md) explains how to set up a development environment, run the tests, and add support for a new engine. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

[Apache License 2.0](LICENSE). The plugins bundle fonts under the SIL Open Font License; see each font's `OFL` file.
