"""`autopilot connect claude|codex`: set up the user's own Claude Code or Codex to work on the game open in the editor
through Autopilot (`autopilot mcp`, autopilot/mcp.py), on the user's own plan. `--remove` takes out what it added;
`autopilot uninstall` does too (remove_all).

    Claude Code  the MCP server, user scope (`claude mcp add --scope user autopilot -- <python> -m autopilot mcp`);
                 the `autopilot` skill (<CLAUDE_CONFIG_DIR, else ~/.claude>/skills/autopilot/SKILL.md); the
                 `frame-describer` subagent (.../agents/frame-describer.md: Opus, the `frames` tool only)
    Codex        the MCP server in <CODEX_HOME, else ~/.codex>/config.toml ([mcp_servers.autopilot], its tools approved:
                 they reach only the game and its memory folder); the skill (~/.agents/skills/autopilot/SKILL.md); the
                 `frame_describer` agent (<CODEX_HOME>/agents/frame_describer.toml)

Claude Code can instead take Autopilot's plugin (plugin/claude-code in the repository, the same skill and subagent, the
server started as `autopilot mcp`): `claude plugin install autopilot --marketplace ramenvr/autopilot`. plugin_files()
is what that folder holds (`python -m autopilot.connect plugin <folder>` writes it).

A file it wrote says so (MARK); one without the mark (the user's own of that name) is never changed or removed.
"""

import json
import os
import re
import shutil
import subprocess
import sys

from . import vision

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "assistants", "SKILL.md")
MARK = "autopilot connect"  # in every file this writes (a comment), and before its Codex config table
NAME = "autopilot"  # the MCP server's name
CLAUDE_DESCRIBER, CODEX_DESCRIBER = "frame-describer", "frame_describer"
DESCRIBER_ABOUT = ("Describes frames of an Autopilot episode (the autopilot `frames` tool) for an agent checking how "
                   "something in a game looks. Give it only the episode, the game times and open questions: never the "
                   "claim, the request or what you expect to see.")
DESCRIBER_HOW = ("You are given an episode, game times and open questions. Call the autopilot `frames` tool with that "
                 "episode, start, end and count, then describe the frames you get, answering each question by its "
                 "number from the frames alone. If `frames` gives no pictures, say what it said instead and stop.")
PLUGIN_SERVER = f"mcp__plugin_{NAME}_{NAME}__frames"  # (a plugin's server's tools are named for the plugin too)
TABLE = re.compile(r"^\s*\[\s*mcp_servers\s*\.\s*(\"autopilot\"|autopilot)\s*(\.[^\]]*)?\]\s*(#.*)?$")
HEADER = re.compile(r"^\s*\[")


def run(cmd):
    """(exit code, output) of a command; (None, why) when it can't start."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def server_command():
    """How the user's agent starts `autopilot mcp`: this Python (the installed command's own, or a checkout's)."""
    return [sys.executable, "-m", "autopilot", "mcp"]


def claude_home():
    return (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip() or os.path.join(os.path.expanduser("~"), ".claude")


def codex_home():
    return (os.environ.get("CODEX_HOME") or "").strip() or os.path.join(os.path.expanduser("~"), ".codex")


def agents_home():
    """Where Codex finds the user's own skills (~/.agents/skills)."""
    return os.path.join(os.path.expanduser("~"), ".agents")


# -- what is written ----------------------------------------------------------------------------------------------

def skill_text():
    with open(SKILL, encoding="utf-8") as f:
        return f.read().rstrip("\n") + f"\n\n<!-- {MARK}: Autopilot's skill -->\n"


def describer_instructions():
    return vision.SYSTEM + "\n\n" + DESCRIBER_HOW


def claude_describer(tool):
    """The Claude Code subagent (tool: the `frames` tool's name as Claude Code knows it)."""
    return (f"---\nname: {CLAUDE_DESCRIBER}\ndescription: {json.dumps(DESCRIBER_ABOUT)}\ntools: {tool}\n"
            "model: opus\n---\n\n"  # (the description quoted: YAML takes no ": " in a plain one)
            f"{describer_instructions()}\n\n<!-- {MARK}: Autopilot's frame describer -->\n")


def codex_describer():
    """The Codex agent: no model named (the user's own, which takes pictures)."""
    return (f"# {MARK}: Autopilot's frame describer\n"
            f"name = {json.dumps(CODEX_DESCRIBER)}\ndescription = {json.dumps(DESCRIBER_ABOUT)}\n"
            f"developer_instructions = '''\n{describer_instructions()}\n'''\n")


def codex_table(command):
    return (f"# {MARK}: Autopilot's MCP server, for the game open in your editor (`autopilot connect codex --remove` "
            f"takes it out)\n[mcp_servers.{NAME}]\ncommand = {json.dumps(command[0])}\n"
            f"args = {json.dumps(command[1:])}\n"
            "# (its tools reach only the game open in the editor and Autopilot's memory folder for it)\n"
            'default_tools_approval_mode = "approve"\n')


def plugin_files():
    """{path in the plugin's folder: text} of Autopilot's Claude Code plugin."""
    from . import __version__

    manifest = {"name": NAME, "version": __version__, "description": "Playtest the game open in your Unreal, Unity, "
                "Godot or Roblox Studio editor with Autopilot, on your own Claude plan.", "author": {"name": "Ramen VR"},
                "homepage": "https://github.com/ramenvr/autopilot", "license": "Apache-2.0"}
    servers = {"mcpServers": {NAME: {"command": "autopilot", "args": ["mcp"]}}}
    return {".claude-plugin/plugin.json": json.dumps(manifest, indent=2) + "\n",
            ".mcp.json": json.dumps(servers, indent=2) + "\n",
            "skills/autopilot/SKILL.md": skill_text(),
            f"agents/{CLAUDE_DESCRIBER}.md": claude_describer(PLUGIN_SERVER)}


def write_plugin(folder):
    for rel, text in plugin_files().items():
        path = os.path.join(folder, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


# -- files that are ours ------------------------------------------------------------------------------------------

def ours(path):
    """Whether the file at path was written by this (it has MARK), or isn't there."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return MARK in f.read()
    except FileNotFoundError:
        return True
    except OSError:
        return False


def put(path, text, say):
    """Write a file of ours (never over the user's own); whether it was written."""
    if not ours(path):
        say(f"Left {path} as it is: it isn't Autopilot's (rename it to have Autopilot's written there)")
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    say(f"Wrote {path}")
    return True


def drop(path, say):
    """Remove a file of ours (and its folder, once empty)."""
    if os.path.exists(path) and ours(path):
        os.remove(path)
        say(f"Removed {path}")
        folder = os.path.dirname(path)
        try:
            os.rmdir(folder)  # (only when empty)
        except OSError:
            pass


# -- Codex's config.toml ------------------------------------------------------------------------------------------

def without_table(text):
    """config.toml's text without the autopilot MCP server's tables (and the comment of ours before them)."""
    out, skipping = [], False
    for line in text.splitlines(keepends=True):
        if TABLE.match(line):
            skipping = True
            while out and out[-1].lstrip().startswith("#") and MARK in out[-1]:
                out.pop()
            continue
        if skipping and HEADER.match(line):
            skipping = False
        if not skipping:
            out.append(line)
    return "".join(out)


def codex_config(text, command):
    """config.toml's text with the autopilot MCP server as connect sets it up (command None: without it). Raises
    ValueError when the result isn't TOML (the server set up some other way: by a dotted key)."""
    import tomllib

    kept = without_table(text).rstrip("\n")
    new = kept + ("\n\n" if kept else "") + codex_table(command) if command else (kept + "\n" if kept else "")
    parsed = tomllib.loads(new)
    if command and (parsed.get("mcp_servers") or {}).get(NAME, {}).get("command") != command[0]:
        raise ValueError("the autopilot server's table didn't come out as written")
    return new


def edit_codex_config(command, say):
    path = os.path.join(codex_home(), "config.toml")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        text = ""
    if not command and not any(TABLE.match(line) for line in text.splitlines()):
        return True  # (nothing of ours there)
    try:
        new = codex_config(text, command)
    except ValueError as e:
        say(f"Couldn't change {path}: it isn't TOML as written here ({e}). Set the server up by hand: "
            f"codex mcp add {NAME} -- {' '.join(server_command())}")
        return False
    if new == text:
        return True
    if text:
        shutil.copy2(path, path + ".before-autopilot")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(new)
    say(f"{'Set up' if command else 'Removed'} the autopilot MCP server in {path}" +
        (f" (the file as it was: {path}.before-autopilot)" if text else ""))
    return True


# -- the commands -------------------------------------------------------------------------------------------------

def claude_server(command, say, runner=None):
    """The MCP server in Claude Code's user scope (command None: removed, when it is Autopilot's). runner: how
    commands run (run, looked up when called: tests never run the real one)."""
    runner = runner or run
    code, out = runner(["claude", "mcp", "get", NAME])
    there = code == 0
    if there and not re.search(r"autopilot\S*\s+mcp\b", out.replace("\\", "/").split(NAME, 1)[-1]):
        say(f"Left Claude Code's {NAME} MCP server as it is: it isn't Autopilot's")
        return False
    if there:
        runner(["claude", "mcp", "remove", "--scope", "user", NAME])
    if not command:
        if there:
            say("Removed the autopilot MCP server from Claude Code")
        return True
    code, out = runner(["claude", "mcp", "add", "--scope", "user", NAME, "--", *command])
    if code is None:
        say("Claude Code's `claude` command isn't on this computer's PATH. Add the server by hand: "
            f"claude mcp add --scope user {NAME} -- {' '.join(command)}")
        return False
    if code != 0:
        say(f"`claude mcp add` failed: {out.strip()[:300]}")
        return False
    say("Added the autopilot MCP server to Claude Code (user scope: every project)")
    return True


def connect(client, say=print, runner=None):
    """Set up client (claude or codex). Returns the exit code."""
    command = server_command()
    if client == "claude":
        ok = claude_server(command, say, runner)
        home = claude_home()
        ok &= put(os.path.join(home, "skills", NAME, "SKILL.md"), skill_text(), say)
        ok &= put(os.path.join(home, "agents", f"{CLAUDE_DESCRIBER}.md"), claude_describer(f"mcp__{NAME}__frames"),
                  say)
        what = "Claude Code"
    else:
        ok = edit_codex_config(command, say)
        ok &= put(os.path.join(agents_home(), "skills", NAME, "SKILL.md"), skill_text(), say)
        ok &= put(os.path.join(codex_home(), "agents", f"{CODEX_DESCRIBER}.toml"), codex_describer(), say)
        what = "Codex"
    say(f"{what} is set up for Autopilot{'' if ok else ', but for what is said above'}. Open the game's project in the "
        f"editor with the Autopilot tab connected, start a new {what} session, and ask it, e.g.: \"Use Autopilot to "
        "verify that the door opens with the red key.\"")
    from . import cli

    missing = cli.decision_key_missing()
    if missing:
        say("Before the first run: " + missing.replace(" Then call start_run again.", ""))
    return 0 if ok else 3


def disconnect(client, say=print, runner=None):
    """Take out what connect(client) set up. Returns the exit code."""
    if client == "claude":
        ok = claude_server(None, say, runner)
        home = claude_home()
        drop(os.path.join(home, "skills", NAME, "SKILL.md"), say)
        drop(os.path.join(home, "agents", f"{CLAUDE_DESCRIBER}.md"), say)
    else:
        ok = edit_codex_config(None, say)
        drop(os.path.join(agents_home(), "skills", NAME, "SKILL.md"), say)
        drop(os.path.join(codex_home(), "agents", f"{CODEX_DESCRIBER}.toml"), say)
    return 0 if ok else 3


def remove_all(say=print, runner=None):
    """What connect set up in Claude Code and Codex, taken out (autopilot uninstall). Whether all of it went."""
    ok = True
    for client in ("claude", "codex"):
        try:
            ok &= disconnect(client, say=say, runner=runner) == 0
        except Exception as e:  # noqa: BLE001 - the rest of the uninstall goes on
            say(f"Couldn't take Autopilot out of {client}: {type(e).__name__}: {e}")
            ok = False
    return ok


def main(argv=None):
    """python -m autopilot.connect plugin <folder>: write Autopilot's Claude Code plugin there."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "plugin":
        write_plugin(argv[1])
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
