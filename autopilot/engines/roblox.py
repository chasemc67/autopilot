"""Roblox Studio: the Autopilot Studio plugin (plugin/roblox, installed with `autopilot roblox install`). It reaches the
brain over HTTP (autopilot/roblox_bridge.py) and answers the engine-neutral ops (find, transforms, pose, movement) in
the kit frame, and `rc` in Roblox's own terms: instance paths from the DataModel (/Workspace/Map/Door, a second
sibling of one name /Workspace/Coins/Coin@2), properties and attributes by name, studs on Roblox's axes.

Roblox's frame: x right, y up, -z forward (a CFrame's LookVector), studs, right-handed; CFrame:ToOrientation() gives
radians in YXZ order, ry turning left (from -z toward -x), rx tilting the view up. 1 stud is 28 cm (Roblox's own
physics conversion: a default character is about 5 studs tall). The kit's: X forward, Y right, Z up, centimetres:
    kit (X, Y, Z) = 28 * (-z, x, y);  kit yaw = -deg(ry);  kit pitch = deg(rx)

The codex: the place's scripts as the plugin exports them from the open place (every LuaSourceContainer's Source, as
AutopilotCodex/scripts/<Service>/<path>.server.luau | .client.luau | .luau) and its summary of the place
(AutopilotCodex/summary.txt), both at the codex's top. From this machine's disk instead (`autopilot codex --project`):
a Rojo project's Luau files, or the scripts of a .rbxlx place file (XML; a binary .rbxl can't be read here).
"""

import os
import re
import sys
import xml.etree.ElementTree as ET

from ..kit.geom import Vec, wrap
from .base import CODEX_OWN, OpEngine, safe_rel

STUD_CM = 28.0  # centimetres in a stud
EXPORT_PREFIX = "AutopilotCodex/"
CODE_EXT = (".lua", ".luau")
PLACE_EXT = (".rbxlx", ".rbxl")
TOP_FILES = ("default.project.json", "wally.toml", "aftman.toml", "rokit.toml", "selene.toml")
SKIP = (".git", "node_modules", "Packages", "DevPackages", "ServerPackages", "build", "dist", ".vscode", ".idea")
SCRIPT_CLASSES = {"Script": ".server.luau", "LocalScript": ".client.luau", "ModuleScript": ".luau"}
# Where Studio keeps plugins (`autopilot roblox install` copies the built plugin there)
PLUGIN_FILE = "Autopilot.server.lua"


def plugins_dir():
    """Roblox Studio's local Plugins folder: ~/Documents/Roblox/Plugins on macOS, %LOCALAPPDATA%\\Roblox\\Plugins on
    Windows (AUTOPILOT_ROBLOX_PLUGINS overrides it). Elsewhere (no Studio): ~/.local/share/Roblox/Plugins, for the
    tests."""
    env = os.environ.get("AUTOPILOT_ROBLOX_PLUGINS")
    if env:
        return env
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local"), "Roblox",
                            "Plugins")
    if sys.platform == "darwin":
        return os.path.join(home, "Documents", "Roblox", "Plugins")
    return os.path.join(home, ".local", "share", "Roblox", "Plugins")


def studio_paths():
    """Where Roblox Studio is usually installed on this kind of machine (the first that exists is the one found)."""
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return ["/Applications/RobloxStudio.app", os.path.join(home, "Applications", "RobloxStudio.app")]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return [os.path.join(local, "Roblox", "Versions"),
                os.path.join(os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)", "Roblox", "Versions")]
    return []


def find_studio(paths=None):
    """Roblox Studio on this machine: the app (macOS) or the RobloxStudioBeta.exe of the newest version folder
    (Windows); AUTOPILOT_ROBLOX_STUDIO when set; None when not found."""
    env = os.environ.get("AUTOPILOT_ROBLOX_STUDIO")
    if env:
        return env if os.path.exists(env) else None
    for path in paths if paths is not None else studio_paths():
        if path.endswith(".app"):
            if os.path.isdir(path):
                return path
            continue
        if not os.path.isdir(path):
            continue
        found = []
        for version in os.listdir(path):
            exe = os.path.join(path, version, "RobloxStudioBeta.exe")
            if os.path.isfile(exe):
                found.append((os.path.getmtime(exe), exe))
        if found:
            return max(found)[1]
    return None


class Roblox(OpEngine):
    name = "roblox"
    title = "Roblox Studio"
    export_dir = EXPORT_PREFIX.rstrip("/")
    error_log = True  # the plugin sends LogService's errors (`log` events)
    plugin_files = ()  # (the plugin keeps nothing in the place: its settings are Studio's plugin settings)

    # -- the project on disk -----------------------------------------------------------------------------

    def project_at(self, path):
        """A Rojo project folder (one with default.project.json, or a *.project.json file itself, as its folder), or a
        place file (.rbxlx / .rbxl) as itself."""
        if not path:
            return None
        path = os.path.abspath(path)
        if os.path.isfile(path):
            low = path.lower()
            if low.endswith(PLACE_EXT):
                return path
            if low.endswith(".project.json"):
                return os.path.dirname(path)
            return None
        if os.path.isfile(os.path.join(path, "default.project.json")):
            return path
        return None

    def project_name(self, project):
        """A Rojo project's `name`, else the place file's or folder's name (what the plugin's hello says is the open
        place's name, which Studio takes from the file)."""
        if os.path.isfile(project):
            return os.path.splitext(os.path.basename(project))[0]
        try:
            import json

            with open(os.path.join(project, "default.project.json"), encoding="utf-8") as f:
                name = json.load(f).get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        except (OSError, ValueError, AttributeError):
            pass
        return super().project_name(project)

    # -- the game backend --------------------------------------------------------------------------------

    def name_of(self, path):
        """The last part of an instance path ("/Workspace/Coins/Coin@2" -> "Coin@2"), without a :sub-path."""
        return str(path).rstrip("/").rsplit("/", 1)[-1].split(":", 1)[0]

    def all_actors(self, game):
        """The place's things, as Unreal's actors: Workspace's direct children and the Models and parts in folders
        under it (not every part of a model)."""
        return list((game.request("find", {"things": True}) or {}).get("objects") or [])

    # -- the codex ---------------------------------------------------------------------------------------

    def codex_path(self, rel):
        """The plugin's export (AutopilotCodex/) at the codex's top; from a project on disk, its Luau files and Rojo
        and tool files at their project paths, outside hidden folders and package folders."""
        parts = safe_rel(rel)
        if parts is None:
            return None
        if rel.startswith(EXPORT_PREFIX) and len(parts) > 1:
            dest = rel[len(EXPORT_PREFIX):]
            return dest if dest not in CODEX_OWN else None
        if any(p.startswith(".") or p in SKIP for p in parts[:-1]):
            return None
        low = rel.lower()
        if low.endswith(CODE_EXT):
            return rel
        if len(parts) == 1 and (rel in TOP_FILES or low.endswith(".project.json")):
            return rel
        if low.endswith((".model.json", ".meta.json")):
            return rel
        return None

    def skip_dir(self, rel):
        name = rel.split("/")[-1]
        return name.startswith(".") or name in SKIP

    def copy_source(self, project, codex_root):
        """A Rojo project's files (as Engine.copy_source), or a place file's scripts (a .rbxlx: scripts/... as the
        plugin's export names them)."""
        if os.path.isfile(project):
            n = 0
            for rel, source in place_scripts(project):
                full = os.path.join(codex_root, "scripts", *rel.split("/"))
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8", newline="") as f:
                    f.write(source)
                n += 1
            return n
        return super().copy_source(project, codex_root)

    def has_export(self, codex_root):
        return os.path.exists(os.path.join(codex_root, "summary.txt"))

    no_export_note = ("note: no summary of the place in the codex (the Roblox plugin makes it from the open place: "
                      "open the Autopilot dock in Studio)")

    def index_notes(self, codex_root):
        """Where to start in a Roblox place: the summary, then the scripts by where they run."""
        lines = ["", "## Where to start", ""]
        if os.path.exists(os.path.join(codex_root, "summary.txt")):
            lines.append("- summary.txt: the place at a glance: each service's tree (folders, models, parts "
                         "counted), RemoteEvents and RemoteFunctions, StarterPlayer's settings (walk speed, jump), "
                         "CollectionService tags, attributes, the texts in StarterGui.")
        lines.append("- scripts/<Service>/...: the place's scripts. `.server.luau` are Scripts (the server's rules), "
                     "`.client.luau` LocalScripts (input, camera, GUI), `.luau` ModuleScripts (required by both). "
                     "ServerScriptService holds the game's rules; StarterPlayer/StarterPlayerScripts and StarterGui "
                     "the player's controls and HUD.")
        lines.append("- Paths in the scripts (`workspace.Coins`, `game.ReplicatedStorage.Remotes.Buy`) are the "
                     "bridge's paths with slashes: `/Workspace/Coins`, `/ReplicatedStorage/Remotes/Buy`.")
        return lines

    # -- the kit frame -----------------------------------------------------------------------------------

    def to_kit(self, v):
        n = Vec.of(v)
        return None if n is None else Vec(-STUD_CM * n.z, STUD_CM * n.x, STUD_CM * n.y)

    def to_native(self, v):
        k = Vec.of(v)
        return {"x": k.y / STUD_CM, "y": k.z / STUD_CM, "z": -k.x / STUD_CM}

    def rotation_to_kit(self, r):
        """An orientation {"x","y","z"} in degrees (BasePart.Orientation, which is YXZ like CFrame:ToOrientation) as
        (yaw, pitch)."""
        r = r or {}
        return wrap(-float(r.get("y", 0.0))), wrap(float(r.get("x", 0.0)))

    def rotation_to_native(self, yaw, pitch=0.0):
        return {"x": pitch, "y": -wrap(yaw), "z": 0.0}


# -- place files ---------------------------------------------------------------------------------------------

def _prop(item, kind, name):
    props = item.find("Properties")
    if props is None:
        return None
    for el in props.findall(kind):
        if el.get("name") == name:
            return el.text or ""
    return None


def _safe(name):
    """An instance name as a file name (no slashes, nothing Windows refuses)."""
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name or "").strip()) or "_"
    return name if name not in (".", "..") else "_"


def place_scripts(path):
    """[(path, source)] of the scripts in a .rbxlx place file: paths as the plugin's export has them
    (<Service>/<Parent>/<Name>.server.luau, a second sibling of one name <Name>@2). A binary .rbxl has none here (read
    it in Studio through the plugin)."""
    if not path.lower().endswith(".rbxlx"):
        return []
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return []
    out = []

    def walk(item, parents):
        names = {}
        for child in item.findall("Item"):
            name = _safe(_prop(child, "string", "Name") or child.get("class"))
            names[name] = names.get(name, 0) + 1
            if names[name] > 1:
                name = f"{name}@{names[name]}"
            cls = child.get("class")
            if cls in SCRIPT_CLASSES:
                source = _prop(child, "ProtectedString", "Source") or ""
                out.append(("/".join(parents + [name]) + SCRIPT_CLASSES[cls], source))
            walk(child, parents + [name])

    walk(root, [])
    return out

