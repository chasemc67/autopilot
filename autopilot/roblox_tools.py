"""`autopilot roblox ...`: the Roblox Studio plugin on this machine (docs/roblox.md).

    autopilot roblox install [--plugins-dir DIR] [--source PATH] [--url URL]
        build plugin/roblox into one Autopilot.server.lua, with this machine's bridge token baked in, and copy it into
        Studio's Plugins folder (~/Documents/Roblox/Plugins on macOS, %LOCALAPPDATA%\\Roblox\\Plugins on Windows)
    autopilot roblox build [--out FILE]      the same file, anywhere (no token unless --with-token)
    autopilot roblox status [--show-token]   Studio, the plugin, the token and the bridge, as found
    autopilot roblox uninstall               take the plugin out of the Plugins folder

The plugin's source is the repository's plugin/roblox (a clone), or --source: a plugin/roblox folder, or an
Autopilot.server.lua built already (its token placeholder is filled in).
"""

import importlib.util
import os
import re

from . import roblox_bridge
from .engines import roblox as robloxeng

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PLACEHOLDER = "__AUTOPILOT_ROBLOX_TOKEN__"


def plugin_source():
    """The repository's plugin/roblox beside this package (a clone), or None (an installed package has no plugin)."""
    path = os.path.join(os.path.dirname(HERE), "plugin", "roblox")
    return path if os.path.isfile(os.path.join(path, "build_plugin.py")) else None


def _builder(source_dir):
    spec = importlib.util.spec_from_file_location("autopilot_roblox_build_plugin",
                                                  os.path.join(source_dir, "build_plugin.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plugin_text(token=None, url=None, source=None):
    """The single-file plugin's text: built from a plugin/roblox folder (source, else the repository's), or a built
    Autopilot.server.lua (source) with the token and address filled in."""
    source = source or plugin_source()
    if not source:
        raise FileNotFoundError("no plugin/roblox here (an installed Autopilot doesn't carry it): give --source, a "
                                "plugin/roblox folder from the repository or a built Autopilot.server.lua")
    if os.path.isfile(source):
        with open(source, encoding="utf-8") as f:
            text = f.read()
        if token:
            text = text.replace(TOKEN_PLACEHOLDER, token)
        if url:
            text = re.sub(r'URL = "[^"\n]*"', lambda m: f'URL = "{url}"', text, count=1)
        return text
    return _builder(source).build(token, url, os.path.join(source, "src"))


def installed_path(plugins_dir=None):
    return os.path.join(plugins_dir or robloxeng.plugins_dir(), robloxeng.PLUGIN_FILE)


def install(plugins_dir=None, source=None, url=None, token=None):
    """Build the plugin with the bridge token (this machine's, made if needed) and put it in the Plugins folder.
    Returns its path."""
    token = token or roblox_bridge.token()
    text = plugin_text(token, url, source)
    path = installed_path(plugins_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)
    return path


def uninstall(plugins_dir=None):
    """Take the plugin out of the Plugins folder. Returns whether there was one."""
    path = installed_path(plugins_dir)
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def baked_token(path):
    """The token baked into an installed plugin file ("" for none, None when there's no file)."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'TOKEN = "([^"\n]*)"', text)
    if not m or m.group(1) == TOKEN_PLACEHOLDER:
        return ""
    return m.group(1)


def status(plugins_dir=None, ping=None):
    """What there is on this machine: {"studio", "plugins_dir", "plugin", "plugin_token": "matches" | "differs" |
    "none" | None, "token_file", "bridge": the bridge's ping answer or None, "bridge_url"}."""
    path = installed_path(plugins_dir)
    token = roblox_bridge.token(create=False)
    baked = baked_token(path)
    if baked is None:
        match = None
    elif not baked:
        match = "none"
    else:
        match = "matches" if token and baked == token else "differs"
    url = f"http://127.0.0.1:{roblox_bridge.port() or roblox_bridge.PORT}{roblox_bridge.PREFIX}"
    return {"studio": robloxeng.find_studio(), "plugins_dir": os.path.dirname(path),
            "plugin": path if os.path.isfile(path) else None, "plugin_token": match,
            "token_file": roblox_bridge.token_path() if token else None,
            "bridge": (ping or roblox_bridge.ping)(url), "bridge_url": url}


def main(args):
    """`autopilot roblox <action>`; the exit code."""
    action = args.action
    if action == "install":
        try:
            path = install(args.plugins_dir, args.source, args.url)
        except (OSError, ValueError) as e:
            print(f"Couldn't install the Roblox plugin: {e}")
            return 1
        print(f"Installed the Autopilot plugin: {path}")
        print("Next: restart Roblox Studio (or reload plugins), allow the plugin's HTTP requests to 127.0.0.1 when "
              "Studio asks, turn on Game Settings > Security > Allow HTTP Requests, run `autopilot serve`, and open "
              "the Autopilot dock (Plugins tab).")
        return 0
    if action == "build":
        token = roblox_bridge.token() if args.with_token else None
        try:
            text = plugin_text(token, args.url, args.source)
        except (OSError, ValueError) as e:
            print(f"Couldn't build the Roblox plugin: {e}")
            return 1
        out = args.out or os.path.abspath(robloxeng.PLUGIN_FILE)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        print(f"Built {out}" + ("" if token else " (no token: enter it in the dock, `autopilot roblox status "
                                                  "--show-token`)"))
        return 0
    if action == "uninstall":
        removed = uninstall(args.plugins_dir)
        print(f"Removed {installed_path(args.plugins_dir)}" if removed else "The Roblox plugin isn't installed here.")
        return 0
    s = status(args.plugins_dir)
    print(f"Roblox Studio: {s['studio'] or 'not found (AUTOPILOT_ROBLOX_STUDIO names it)'}")
    print(f"Plugins folder: {s['plugins_dir']}")
    if s["plugin"]:
        words = {"matches": "this machine's token baked in", "differs": "a different token baked in: run "
                 "`autopilot roblox install` again", "none": "no token baked in: enter it in the dock"}
        print(f"Plugin: {s['plugin']} ({words.get(s['plugin_token'], '?')})")
    else:
        print("Plugin: not installed (`autopilot roblox install`)")
    print(f"Token: {s['token_file'] or 'none yet (made by `autopilot roblox install` or `autopilot serve`)'}")
    if getattr(args, "show_token", False):
        print(f"  {roblox_bridge.token()}")
    bridge = s["bridge"]
    print(f"Bridge: {s['bridge_url']}: " + ("answering (`autopilot serve` is running)" if bridge else
                                            "not answering (start `autopilot serve`)"))
    return 0
