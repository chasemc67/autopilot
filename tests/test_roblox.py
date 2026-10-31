"""Roblox Studio without Studio: the engine adapter (autopilot/engines/roblox.py), the HTTP bridge the plugin long-polls
(autopilot/roblox_bridge.py) driven end to end by a fake plugin, `autopilot roblox install|status|build`
(autopilot/roblox_tools.py), and checks on the plugin's Luau source (plugin/roblox)."""

import base64
import os
import re
import stat
import struct
import sys
import threading
import time
import types

import pytest
import requests

from autopilot import bridge, engines, roblox_bridge, roblox_tools
from autopilot.engines import roblox as robloxmod
from autopilot.kit.game import Game, GameError
from autopilot.kit.geom import Vec

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin", "roblox")
SRC = os.path.join(PLUGIN, "src")
TOKEN = "test-token-123"


def _src(name):
    with open(os.path.join(SRC, name), encoding="utf-8") as f:
        return f.read()


# -- the engine ----------------------------------------------------------------------------------------------

def test_roblox_is_an_engine():
    eng = engines.get("roblox")
    assert eng.name == "roblox" and eng.title == "Roblox Studio" and "roblox" in engines.ENGINES
    assert engines.for_hello({"engine": "Roblox"}).name == "roblox"
    assert isinstance(eng, engines.OpEngine)
    # (the engine-neutral ops are required of it, as of Unity's and Godot's plugins)
    assert {"find", "transforms", "pose", "movement"} <= set(bridge.required_ops({"engine": "roblox"}))
    game = Game(types.SimpleNamespace(hello={"project": "Obby", "engine": "roblox"}))
    assert game.engine.name == "roblox" and game.keys["jump"] == "SpaceBar"


def _rojo(tmp_path):
    d = tmp_path / "obby"
    for sub in ("src/server", "src/client", "src/shared", "Packages/x", ".git", "build"):
        (d / sub).mkdir(parents=True)
    (d / "default.project.json").write_text('{"name": "Lava Obby", "tree": {"$className": "DataModel"}}')
    (d / "src" / "server" / "Main.server.luau").write_text("print('server')")
    (d / "src" / "client" / "Controls.client.lua").write_text("print('client')")
    (d / "src" / "shared" / "Util.luau").write_text("return {}")
    (d / "src" / "shared" / "Stage.model.json").write_text("{}")
    (d / "Packages" / "x" / "lib.luau").write_text("return 1")
    (d / ".git" / "x.luau").write_text("nope")
    (d / "build" / "out.luau").write_text("nope")
    (d / "wally.toml").write_text("[package]")
    (d / "icon.png").write_bytes(b"\x89PNG")
    return d


RBXLX = """<roblox version="4">
  <Item class="Workspace" referent="RBX1">
    <Properties><string name="Name">Workspace</string></Properties>
    <Item class="Part" referent="RBX2">
      <Properties><string name="Name">Lava</string></Properties>
      <Item class="Script" referent="RBX3">
        <Properties><string name="Name">Kill</string><ProtectedString name="Source"><![CDATA[script.Parent.Touched:Connect(function() end)]]></ProtectedString></Properties>
      </Item>
    </Item>
  </Item>
  <Item class="ServerScriptService" referent="RBX4">
    <Properties><string name="Name">ServerScriptService</string></Properties>
    <Item class="ModuleScript" referent="RBX5">
      <Properties><string name="Name">Rounds</string><ProtectedString name="Source">return {}</ProtectedString></Properties>
    </Item>
    <Item class="ModuleScript" referent="RBX6">
      <Properties><string name="Name">Rounds</string><ProtectedString name="Source">return 2</ProtectedString></Properties>
    </Item>
  </Item>
  <Item class="StarterPlayer" referent="RBX7">
    <Properties><string name="Name">StarterPlayer</string></Properties>
    <Item class="StarterPlayerScripts" referent="RBX8">
      <Properties><string name="Name">StarterPlayerScripts</string></Properties>
      <Item class="LocalScript" referent="RBX9">
        <Properties><string name="Name">Sprint/Run</string><ProtectedString name="Source">-- sprint</ProtectedString></Properties>
      </Item>
    </Item>
  </Item>
</roblox>
"""  # noqa: E501 (a place file as Studio writes it)


def test_find_project_rojo_and_place_files(tmp_path):
    d = _rojo(tmp_path)
    for p in (d, d / "default.project.json"):
        eng, path = engines.find_project(str(p))
        assert eng.name == "roblox" and os.path.samefile(path, d), p
    assert engines.get("roblox").project_name(str(d)) == "Lava Obby"
    place = tmp_path / "Tycoon.rbxlx"
    place.write_text(RBXLX, encoding="utf-8")
    eng, path = engines.find_project(str(place))
    assert eng.name == "roblox" and path == str(place)
    assert eng.project_name(path) == "Tycoon" and eng.project_folder(path) == str(tmp_path)
    assert engines.get("roblox").project_at(str(tmp_path / "nothing")) is None


def test_codex_rules():
    rb = engines.get("roblox")
    assert rb.codex_path("AutopilotCodex/summary.txt") == "summary.txt"
    assert rb.codex_path("AutopilotCodex/scripts/ServerScriptService/Main.server.luau") == \
        "scripts/ServerScriptService/Main.server.luau"
    for ok in ("src/server/Main.server.luau", "src/client/Controls.client.lua", "default.project.json",
               "wally.toml", "src/shared/Stage.model.json", "dev.project.json"):
        assert rb.codex_path(ok) == ok, ok
    for bad in ("AutopilotCodex/README.md", "Packages/x/lib.luau", ".git/x.luau", "build/out.luau", "icon.png",
                "../x.luau", "/abs.luau", "C:/x.luau", "a\\b.luau", "sub/wally.toml"):
        assert rb.codex_path(bad) is None, bad


def test_local_codex_from_a_rojo_project(tmp_path):
    rb = engines.get("roblox")
    root = tmp_path / "codex"
    root.mkdir()
    n = rb.copy_source(str(_rojo(tmp_path)), str(root))
    have = sorted(str(p.relative_to(root)).replace(os.sep, "/") for p in root.rglob("*") if p.is_file())
    assert have == ["default.project.json", "src/client/Controls.client.lua", "src/server/Main.server.luau",
                    "src/shared/Stage.model.json", "src/shared/Util.luau", "wally.toml"] and n == 6


def test_place_file_scripts(tmp_path):
    place = tmp_path / "Tycoon.rbxlx"
    place.write_text(RBXLX, encoding="utf-8")
    got = dict(robloxmod.place_scripts(str(place)))
    assert got == {
        "Workspace/Lava/Kill.server.luau": "script.Parent.Touched:Connect(function() end)",
        "ServerScriptService/Rounds.luau": "return {}",
        "ServerScriptService/Rounds@2.luau": "return 2",  # (a second sibling of one name, as the plugin's paths)
        "StarterPlayer/StarterPlayerScripts/Sprint_Run.client.luau": "-- sprint",
    }
    root = tmp_path / "codex"
    root.mkdir()
    assert engines.get("roblox").copy_source(str(place), str(root)) == 4
    assert (root / "scripts" / "Workspace" / "Lava" / "Kill.server.luau").read_text() == \
        "script.Parent.Touched:Connect(function() end)"
    assert robloxmod.place_scripts(str(tmp_path / "x.rbxl")) == []  # (binary: read through the plugin)
    (tmp_path / "bad.rbxlx").write_text("<roblox", encoding="utf-8")
    assert robloxmod.place_scripts(str(tmp_path / "bad.rbxlx")) == []


def test_codex_index(tmp_path):
    rb = engines.get("roblox")
    root = tmp_path / "codex"
    (root / "scripts" / "ServerScriptService").mkdir(parents=True)
    (root / "scripts" / "ServerScriptService" / "Main.server.luau").write_text("print(1)")
    (root / "summary.txt").write_text("Roblox place: Obby")
    rb.write_index(str(root), "Obby")
    text = (root / "README.md").read_text(encoding="utf-8")
    assert "Roblox Studio project" in text and ".server.luau" in text and "summary.txt" in text
    assert "/ReplicatedStorage/Remotes/Buy" in text
    assert rb.has_export(str(root))
    os.remove(root / "summary.txt")
    assert not rb.has_export(str(root))


def test_roblox_frame():
    rb = engines.get("roblox")
    assert rb.to_kit({"x": 0, "y": 0, "z": -1}) == Vec(28, 0, 0)  # (forward one stud: 28 cm)
    assert rb.to_kit({"x": 1, "y": 0, "z": 0}) == Vec(0, 28, 0)
    assert rb.to_kit({"x": 0, "y": 2, "z": 0}) == Vec(0, 0, 56)
    assert rb.to_native(Vec(56, 28, 14)) == {"x": 1.0, "y": 0.5, "z": -2.0}
    for v in (Vec(100, -250, 33), Vec(0, 0, 0), Vec(-7, 3, 1000)):
        back = rb.to_kit(rb.to_native(v))
        assert back.dist(v) < 1e-9
    assert rb.to_kit(None) is None
    yaw, pitch = rb.rotation_to_kit({"x": 10, "y": -90, "z": 0})  # (turned right a quarter, looking up 10)
    assert yaw == 90 and pitch == 10
    assert rb.rotation_to_kit(rb.rotation_to_native(45, -5)) == (45, -5)
    # the same axes as Godot's (right-handed, -z forward), a stud instead of a metre
    gd = engines.get("godot")
    v = {"x": 1.5, "y": -2, "z": 3}
    assert rb.to_kit(v).dist(gd.to_kit(v) * (28 / 100)) < 1e-9


def test_roblox_game_backend_over_the_engine_ops():
    class Link:
        hello = {"project": "Obby", "engine": "roblox"}

        def request(self, op, args=None, timeout=None):
            if op == "find":
                return {"objects": ["/Workspace/Stage1", "/Workspace/Coins/Coin@2"]}
            if op == "transforms":
                return {"objects": {o: {"location": {"X": 28, "Y": 0, "Z": 0}} for o in args["objects"]}}
            if op == "pose":
                return {"at": {"X": 0, "Y": 0, "Z": 84}, "eye": {"X": 0, "Y": 0, "Z": 140}, "yaw": 90, "pitch": 0}
            raise AssertionError(op)

    game = Game(Link())
    assert game.all_actors() == ["/Workspace/Stage1", "/Workspace/Coins/Coin@2"]
    assert game.named("coin") == ["/Workspace/Coins/Coin@2"]
    assert game.name_of("/Workspace/Coins/Coin@2") == "Coin@2"
    assert game.loc("/Workspace/Stage1") == Vec(28, 0, 0)
    assert game.pose()["at"] == Vec(0, 0, 84) and game.view_rotation() == (90.0, 0.0)


def test_prompt_roblox_section_is_general():
    from autopilot.agents import session

    text = session.system_prompt(engines.get("roblox"), "runs/r")
    section = text[text.index("## The engine: Roblox Studio"):]
    for needle in ("/Workspace/", "@2", "tag:", "to_kit", "28", "Humanoid:Move", "ProximityPrompt",
                   "ContextActionService", "501", "Pivot", "args="):
        assert needle in section, needle
    # (nothing of a particular game)
    for word in ("Lava", "Obby", "Tycoon"):
        assert not re.search(rf"\b{word}\b", section), word


def test_teleport_moves_a_roblox_models_pivot(monkeypatch):
    from autopilot import research

    sets = []

    class FakeGame:
        engine = engines.get("roblox")
        pawn = "/Workspace/Player1"
        writes = []

        def to_native(self, v):
            return self.engine.to_native(v)

        def set(self, obj, prop, value, why=""):
            sets.append((obj, prop, value))

        def loc(self, actor):
            return Vec(280, 0, 0)

    monkeypatch.setattr(research.time, "sleep", lambda s: None)
    out = research.teleport(FakeGame(), Vec(280, 0, 0))
    assert sets == [("/Workspace/Player1", "Pivot", {"x": 0.0, "y": 0.0, "z": -10.0})] and out["landed"]


# -- Studio on this machine ------------------------------------------------------------------------------------

def test_plugins_dir_per_platform(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOPILOT_ROBLOX_PLUGINS", raising=False)
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / "home") if p == "~" else p)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert robloxmod.plugins_dir() == os.path.join(str(tmp_path / "home"), "Documents", "Roblox", "Plugins")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert robloxmod.plugins_dir() == os.path.join(str(tmp_path / "Local"), "Roblox", "Plugins")
    monkeypatch.setenv("AUTOPILOT_ROBLOX_PLUGINS", str(tmp_path / "mine"))
    assert robloxmod.plugins_dir() == str(tmp_path / "mine")


def test_find_studio(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOPILOT_ROBLOX_STUDIO", raising=False)
    app = tmp_path / "RobloxStudio.app"
    versions = tmp_path / "Versions"
    old, new = versions / "version-aaa", versions / "version-bbb"
    for v in (old, new):
        v.mkdir(parents=True)
        (v / "RobloxStudioBeta.exe").write_text("exe")
    os.utime(old / "RobloxStudioBeta.exe", (1, 1))
    (versions / "version-ccc").mkdir()  # (a player's version folder: no Studio in it)
    assert robloxmod.find_studio([str(app), str(versions)]) == str(new / "RobloxStudioBeta.exe")
    app.mkdir()
    assert robloxmod.find_studio([str(app), str(versions)]) == str(app)
    assert robloxmod.find_studio([str(tmp_path / "none")]) is None
    monkeypatch.setenv("AUTOPILOT_ROBLOX_STUDIO", str(app))
    assert robloxmod.find_studio([]) == str(app)
    monkeypatch.setattr(sys, "platform", "darwin")
    assert robloxmod.studio_paths()[0] == "/Applications/RobloxStudio.app"


# -- the token, install, build, status --------------------------------------------------------------------------

def test_the_token_is_made_once_and_kept_private(tmp_path, monkeypatch):
    path = str(tmp_path / "roblox_token")
    assert roblox_bridge.token(path, create=False) is None
    made = roblox_bridge.token(path)
    assert re.fullmatch(r"[0-9a-f]{32}", made) and roblox_bridge.token(path) == made
    if os.name != "nt":
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    monkeypatch.setenv("AUTOPILOT_ROBLOX_TOKEN", "from-env")
    assert roblox_bridge.token(path) == "from-env"


def test_install_bakes_the_token_into_the_plugins_folder(tmp_path):
    plugins = tmp_path / "Plugins"
    path = roblox_tools.install(str(plugins))
    assert path == str(plugins / "Autopilot.server.lua")
    text = open(path, encoding="utf-8").read()
    token = roblox_bridge.token()
    assert f'TOKEN = "{token}"' in text and text.count(token) == 1  # (in Config only: never copied into the place)
    assert roblox_tools.baked_token(path) == token
    s = roblox_tools.status(str(plugins), ping=lambda url: None)
    assert s["plugin"] == path and s["plugin_token"] == "matches" and s["bridge"] is None
    assert s["bridge_url"].endswith(":8766/v1/roblox")  # (AUTOPILOT_ROBLOX_PORT=0 in the tests: the default)
    assert roblox_tools.uninstall(str(plugins)) and not os.path.exists(path)
    assert not roblox_tools.uninstall(str(plugins))
    assert roblox_tools.status(str(plugins), ping=lambda url: {"ok": True})["plugin_token"] is None


def test_install_from_a_built_file(tmp_path):
    built = tmp_path / "Autopilot.server.lua"
    built.write_text(roblox_tools.plugin_text(), encoding="utf-8")  # (no token: the placeholder stays)
    assert roblox_tools.baked_token(str(built)) == ""
    path = roblox_tools.install(str(tmp_path / "P"), source=str(built), url="http://127.0.0.1:9999/v1/roblox",
                                token="abc")
    text = open(path, encoding="utf-8").read()
    assert 'TOKEN = "abc"' in text and 'URL = "http://127.0.0.1:9999/v1/roblox"' in text
    s = roblox_tools.status(str(tmp_path / "P"), ping=lambda url: None)
    assert s["plugin_token"] == "differs"  # (not this machine's token)


def test_cli_roblox_commands(tmp_path, capsys):
    from autopilot import cli

    plugins = str(tmp_path / "Plugins")
    assert cli.main(["roblox", "install", "--plugins-dir", plugins]) == 0
    assert os.path.isfile(os.path.join(plugins, "Autopilot.server.lua"))
    assert cli.main(["roblox", "status", "--plugins-dir", plugins, "--show-token"]) == 0
    out = capsys.readouterr().out
    assert "this machine's token baked in" in out and roblox_bridge.token() in out
    out_file = str(tmp_path / "b" / "A.server.lua")
    assert cli.main(["roblox", "build", "--out", out_file]) == 0
    assert roblox_tools.baked_token(out_file) == ""
    assert cli.main(["roblox", "uninstall", "--plugins-dir", plugins]) == 0
    assert not os.path.exists(os.path.join(plugins, "Autopilot.server.lua"))


# -- the single-file build --------------------------------------------------------------------------------------

def _builder():
    return roblox_tools._builder(PLUGIN)


def test_single_file_build_carries_every_module():
    b = _builder()
    text = b.build("tok_1", None)
    mods = b.modules()
    for name in mods:
        if name in b.SOURCE_ONLY:
            assert f'__SOURCES["{name}"]' in text and f'__MODULES["{name}"]' not in text
        else:
            assert f'__MODULES["{name}"] = function()' in text, name
    # every require goes through __require, and every one it needs is there
    body = text.split('__MODULES["', 1)[1]
    assert "require(script.Parent" not in re.sub(r'__SOURCES\["\w+"\] = \[=*\[.*?\]=*\]', "", body, flags=re.S)
    for used in re.findall(r'__require\("(\w+)"\)', text):
        assert used in mods, used
    assert text.count("tok_1") == 1 and "__AUTOPILOT_ROBLOX_TOKEN__" not in text
    assert "\nexport type" not in body  # (export is for a module's top level: none inside the functions)
    assert text.rstrip().endswith("end")  # (the entry's if-block closes the file)
    with pytest.raises(ValueError):
        b.build('bad"token', None)


def test_long_strings_never_close_early():
    b = _builder()
    text = 'a ]] b ]=] c'
    s = b.long_string(text)
    assert s.startswith("[==[\n") and s.endswith("]==]") and s[5:-4] == text


# -- the bridge: one session, no HTTP ----------------------------------------------------------------------------

def _session(**kw):
    return roblox_bridge.Session({"type": "hello", "protocol": 1, "engine": "roblox", "project": "Obby",
                                  "features": ["timed_hold"]}, **kw)


def test_requests_are_routed_to_the_right_channel():
    s = _session(screenshots=False)
    assert s.link.hello["engine"] == "roblox" and "media" not in s.link.hello["features"]
    assert s.target("codex_manifest", {}) == "edit"
    assert s.target("capture", {}) == "host" and s.target("record", {"action": "start"}) == "host"
    assert s.target("session", {"action": "start"}) == "edit"
    assert s.target("session", {"action": "restart"}) == "host"
    assert s.target("context", {}) == "edit" and s.target("ping", {}) == "edit"
    assert s.target("find", {}) is None and s.target("key", {}) is None  # (no play test: 409)
    s.attach("server")
    assert s.target("find", {}) == "server" and s.target("key", {}) == "server" and s.target("context", {}) == "server"
    assert s.target("session", {"action": "status"}) == "host" and s.target("session", {"action": "stop"}) == "server"
    s.attach("client")
    assert s.target("key", {}) == "client" and s.target("pose", {}) == "client" and s.target("rc", {}) == "server"
    s.detach("server", "the play test ended")
    assert s.target("rc", {}) == "client"


def test_no_game_answers_409_at_once_and_frames_queue_for_edit():
    s = _session(screenshots=False)
    started = time.time()
    with pytest.raises(bridge.BridgeError) as e:
        s.link.request("find", {"class": "Part"}, timeout=5)
    assert e.value.status == 409 and "No game is running" in e.value.error and time.time() - started < 1
    s.link.status("Reading the game's code")
    assert s.take("edit", 0) == [{"type": "status", "text": "Reading the game's code"}]


def test_capture_is_the_hosts_and_off_by_default():
    s = _session(screenshots=False)
    with pytest.raises(bridge.BridgeError) as e:
        s.link.request("capture", {"max_dim": 640}, timeout=5)
    assert e.value.status == 409 and "AUTOPILOT_ROBLOX_SCREENSHOT" in e.value.error
    with pytest.raises(bridge.BridgeError):
        s.link.request("record", {"action": "start"}, timeout=5)
    assert s.link.request("record", {"action": "stop"}, timeout=5) == {"recording": False}
    shots = []
    s2 = _session(screenshots=True, shoot=lambda max_dim: shots.append(max_dim) or {"jpeg": "eA==", "width": 1})
    assert "media" in s2.link.hello["features"]  # (the brain may take stills: the host takes them)
    assert s2.link.request("capture", {"max_dim": 640}, timeout=5)["jpeg"] == "eA==" and shots == [640]


def _jpeg(width, height):
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00" + b"\x00" * 9 + sof + b"\xff\xd9"


def test_jpeg_size_and_the_screenshot_command(monkeypatch, tmp_path):
    assert roblox_bridge.jpeg_size(_jpeg(1920, 1080)) == (1920, 1080)
    assert roblox_bridge.jpeg_size(b"not a jpeg") == (None, None)
    ran = []

    def run(cmd, **kw):
        ran.append(cmd)
        if cmd[0] == "screencapture":
            with open(cmd[-1], "wb") as f:
                f.write(_jpeg(800, 600))
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(sys, "platform", "darwin")
    shot = roblox_bridge.screenshot(640, run=run)
    assert ran[0][:4] == ["screencapture", "-x", "-t", "jpg"] and (shot["width"], shot["height"]) == (800, 600)
    assert base64.b64decode(shot["jpeg"])[:2] == b"\xff\xd8" and shot["source"] == "host screenshot"
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(bridge.BridgeError):
        roblox_bridge.screenshot(run=run)


class FakeStudio:
    """autopilot/studio_mcp.StudioMCP's stand-in: Roblox Studio's MCP server."""

    available = True

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def capture(self, name):
        self.calls.append(("capture", name))
        return _jpeg(1292, 648)

    def keys(self, name, actions):
        if self.fail:
            from autopilot.studio_mcp import StudioMCPError

            raise StudioMCPError(self.fail)
        self.calls.append(("keys", name, actions))

    def mouse(self, name, actions):
        self.calls.append(("mouse", name, actions))

    def close(self):
        pass


def _play(s, answer, context="server"):
    """A play channel answering the session's requests with answer(op, args) until the returned event is set."""
    s.attach(context)
    stop = threading.Event()

    def run():
        while not stop.is_set():
            for f in s.take(context, 0.1):
                if f.get("type") == "request":
                    got = answer(f["op"], f.get("args") or {})
                    s.incoming(context, {"type": "response", "id": f["id"], "ok": True, "result": got})

    threading.Thread(target=run, daemon=True).start()
    return stop


def test_picture_mode_follows_the_environment():
    assert roblox_bridge.picture_mode(True, {}) == "studio"
    assert roblox_bridge.picture_mode(False, {}) is None
    assert roblox_bridge.picture_mode(True, {roblox_bridge.SCREENSHOT_ENV: "screen"}) == "screen"
    assert roblox_bridge.picture_mode(False, {roblox_bridge.SCREENSHOT_ENV: "1"}) == "screen"
    assert roblox_bridge.picture_mode(True, {roblox_bridge.SCREENSHOT_ENV: "0"}) is None


def test_pictures_and_recording_come_from_studio_mcp(monkeypatch):
    monkeypatch.delenv(roblox_bridge.SCREENSHOT_ENV, raising=False)
    studio = FakeStudio()
    s = _session(studio=studio)
    assert s.pictures == "studio" and "media" in s.link.hello["features"] and s.real_input
    shot = s.link.request("capture", {"max_dim": 1280}, timeout=5)
    assert (shot["width"], shot["height"]) == (1292, 648) and "StudioMCP" in shot["source"]
    assert studio.calls[0] == ("capture", "Obby")  # (the Studio whose place is the plugin's project)
    frames = []
    s.link.frame_listeners.append(frames.append)
    assert s.link.request("record", {"action": "start", "fps": 30}, timeout=5)["fps"] == roblox_bridge.MAX_RECORD_FPS
    assert s.wait_for(lambda: len(frames) >= 2, 5)
    assert s.link.request("record", {"action": "stop"}, timeout=5) == {"recording": False}
    assert frames[0]["type"] == "frame" and frames[0]["n"] == 1 and frames[0]["jpeg"] and frames[0]["wall"]
    assert _session(studio=types.SimpleNamespace(available=False)).pictures is None


def test_real_keys_clicks_and_actions_go_through_studio_mcp():
    studio = FakeStudio()
    s = _session(studio=studio)
    asked = []

    def answer(op, args):
        asked.append((op, args))
        if op == "ui_act":
            return {"id": args["id"], "click_path": "LocalPlayer.PlayerGui.Hud.Shop"}
        if op == "input":
            return {"host_keys": ["F"]}
        return {"did": "semantic"}

    assert s.target("key", {"key": "E"}) is None  # (no play test: 409, real input or not)
    stop = _play(s, answer)
    try:
        assert s.target("key", {"key": "W"}) == "server" and s.target("key", {"key": "SpaceBar"}) == "server"
        assert s.target("key", {"key": "E"}) == "host" and s.target("ui_act", {}) == "host"
        assert s.link.request("key", {"key": "E"}, timeout=5) == {"did": "real key E"}
        assert studio.calls[-1][2][0] == {"action": "keyDown", "key_code": "E"}
        s.link.request("key", {"key": "Enter"}, timeout=5)
        assert studio.calls[-1][2][-1] == {"action": "keyUp", "key_code": "Return"}  # (Unreal names: KeyCode names)
        assert s.link.request("key", {"key": "W", "mode": "hold"}, timeout=5) == {"did": "semantic"}
        clicked = s.link.request("ui_act", {"id": "Hud.Shop", "action": "click"}, timeout=5)
        assert clicked["clicked"] == "Hud.Shop" and asked[-1] == ("ui_act", {"id": "Hud.Shop", "action": "click",
                                                                             "host_input": True})
        assert studio.calls[-1][2][0] == {"action": "moveTo", "instance_path": "LocalPlayer.PlayerGui.Hud.Shop"}
        assert s.link.request("input", {"action": "Wave"}, timeout=5)["did"].startswith("real key F")
        # A leased hold: down once, renewed, up when the lease runs out
        studio.calls.clear()
        s.link.request("key", {"key": "Q", "mode": "hold", "lease": 0.3}, timeout=5)
        s.link.request("key", {"key": "Q", "mode": "hold"}, timeout=5)
        assert [c[2][0]["action"] for c in studio.calls] == ["keyDown"]
        assert s.wait_for(lambda: not s.held, 3)
        time.sleep(0.1)
        assert studio.calls[-1][2] == [{"action": "keyUp", "key_code": "Q"}]
        # release_all lets go of the real keys and passes on to the game's agent
        s.link.request("key", {"key": "R", "mode": "hold", "seconds": 30}, timeout=5)
        s.link.request("key", {"mode": "release_all"}, timeout=5)
        assert not s.held and studio.calls[-1][2] == [{"action": "keyUp", "key_code": "R"}]
        assert asked[-1] == ("key", {"mode": "release_all"})
    finally:
        stop.set()


def test_a_real_key_studio_mcp_refuses_is_pressed_by_the_game_s_agent():
    s = _session(studio=FakeStudio(fail="no Roblox Studio with the place 'Obby'"))
    stop = _play(s, lambda op, args: {"note": "E isn't bound to anything"})
    try:
        answer = s.link.request("key", {"key": "E"}, timeout=5)
        assert "no real key" in answer["note"] and "Obby" in answer["note"]
    finally:
        stop.set()


def test_studio_mcp_picks_the_studio_with_the_plugin_s_place(tmp_path):
    from autopilot import studio_mcp

    assert studio_mcp.find({studio_mcp.ENV: "0"}) is None
    assert studio_mcp.find({studio_mcp.ENV: str(tmp_path / "nope")}) is None
    exe = tmp_path / "StudioMCP"
    exe.write_text("")
    assert studio_mcp.find({studio_mcp.ENV: str(exe)}) == str(exe)
    sm = studio_mcp.StudioMCP(path=str(exe))
    listed = {"studios": [{"id": "a", "name": "VR Mini Golf"}, {"id": "b", "name": "Place1"}]}
    sm.call = lambda tool, args, timeout=None: [{"type": "text", "text": __import__("json").dumps(listed)}]
    assert sm.choose("Place1") == "b"
    sm.studio = None
    with pytest.raises(studio_mcp.StudioMCPError):
        sm.choose("Obby")  # (never another place's Studio)
    listed = {"studios": [{"id": "c", "name": "Obby.rbxl"}]}
    assert sm.choose("Obby") == "c"


def test_a_channel_that_stops_polling_is_dropped_and_its_requests_fail():
    s = _session(screenshots=False)
    s.attach("server")
    got = {}

    def ask():
        try:
            s.link.request("watch", {"objects": [], "seconds": 1}, timeout=10)
        except bridge.BridgeError as e:
            got["error"] = e

    t = threading.Thread(target=ask)
    t.start()
    assert s.wait_for(lambda: len(s.channels["server"].frames) == 1, 2)
    s.reap(now=time.monotonic() + roblox_bridge.CHANNEL_TIMEOUT + 1)  # (the play test's server went quiet)
    t.join(5)
    assert got["error"].status == 409 and "stopped polling" in got["error"].error
    assert s.closed  # (the edit channel went quiet too: the session ends)


# -- the bridge over HTTP, with a fake plugin ---------------------------------------------------------------------

class FakePlugin:
    """What plugin/roblox's Link.luau does, in Python: hello or attach, then exchange frames."""

    def __init__(self, url, context="edit", token=TOKEN, session=None):
        self.url, self.context, self.token, self.session = url, context, token, session
        self.http = requests.Session()

    def post(self, name, body):
        return self.http.post(f"{self.url}/{name}", json=body, headers={"X-Autopilot-Token": self.token}, timeout=30)

    def hello(self, **extra):
        r = self.post("hello", {"type": "hello", "protocol": 1, "engine": "roblox", "project": "Obby",
                                "host": "editor", "features": ["timed_hold"], **extra})
        r.raise_for_status()
        self.session = r.json()["session"]
        return r.json()

    def attach(self):
        r = self.post("attach", {"session": self.session, "context": self.context})
        r.raise_for_status()
        self.session = r.json()["session"]

    def exchange(self, frames=(), wait=0.0):
        r = self.post("exchange", {"session": self.session, "context": self.context, "frames": list(frames),
                                   "wait": wait})
        r.raise_for_status()
        return r.json()["frames"]

    def serve(self, answer, stop, wait=0.3):
        """Answer requests until stop is set (or the bridge drops this channel): answer(frame) -> a result, or an
        (error, status) tuple. Answers go out with the next exchange."""
        out = []
        while not stop.is_set():
            try:
                frames = self.exchange(out, 0 if out else wait)
            except requests.RequestException:
                return
            out = []
            for f in frames:
                if f.get("type") != "request":
                    continue
                got = answer(f)
                if isinstance(got, tuple):
                    out.append({"type": "response", "id": f["id"], "ok": False, "error": got[0], "status": got[1]})
                else:
                    out.append({"type": "response", "id": f["id"], "ok": True, "result": got, "t": 1.5})


@pytest.fixture
def rbx():
    links = []
    rb = roblox_bridge.RobloxBridge(links.append, port=0, secret=TOKEN, screenshots=False, studio=None)
    rb.links = links
    yield rb
    rb.shutdown()


def test_ping_and_the_token(rbx):
    assert requests.get(f"{rbx.url}/ping", timeout=5).json() == {"ok": True, "protocol": 1,
                                                                 "service": "autopilot-roblox"}
    assert roblox_bridge.ping(rbx.url) == {"ok": True, "protocol": 1, "service": "autopilot-roblox"}
    bad = FakePlugin(rbx.url, token="wrong")
    r = bad.post("hello", {"type": "hello", "protocol": 1})
    assert r.status_code == 401 and "token rejected" in r.json()["error"] and not rbx.links
    r = requests.post(f"{rbx.url}/hello", json={"type": "hello", "protocol": 1}, timeout=5)
    assert r.status_code == 401
    r = FakePlugin(rbx.url).post("hello", {"type": "hello", "protocol": 99})
    assert r.status_code == 400 and not rbx.links
    r = FakePlugin(rbx.url).post("nope", {})
    assert r.status_code == 404
    assert roblox_bridge.ping("http://127.0.0.1:9/v1/roblox", timeout=0.5) is None


def test_a_fake_plugin_end_to_end(rbx):
    edit = FakePlugin(rbx.url)
    welcome = edit.hello()
    assert welcome["type"] == "welcome" and welcome["session"].startswith("roblox-")
    assert "steer" in welcome["features"]
    link = rbx.links[0]
    assert link.hello["engine"] == "roblox" and link.loopback and link.session == welcome["session"]
    stop = threading.Event()

    def edit_answers(f):
        if f["op"] == "context":
            return {"world": "", "character": "", "engine": "roblox", "session_running": False}
        if f["op"] == "session":
            return {"running": False, "loading": False}
        if f["op"] == "codex_manifest":
            return {"project": "Obby", "files": [{"path": "AutopilotCodex/summary.txt", "size": 5, "hash": "h"}]}
        return (f"unknown op {f['op']}", 400)

    t = threading.Thread(target=edit.serve, args=(edit_answers, stop), daemon=True)
    t.start()
    try:
        assert link.request("context", timeout=10)["engine"] == "roblox"
        assert link.request("session", {"action": "status"}, timeout=10) == {"running": False, "loading": False}
        assert link.request("codex_manifest", {}, timeout=10)["project"] == "Obby"
        with pytest.raises(bridge.BridgeError) as e:
            link.request("find", {"things": True}, timeout=10)  # (no play test: answered by the bridge)
        assert e.value.status == 409

        # A play test's server attaches and answers the game's requests
        server = FakePlugin(rbx.url, context="server", session=link.session)
        server.attach()
        seen = []

        def server_answers(f):
            seen.append((f["op"], f["args"]))
            if f["op"] == "find":
                return {"objects": ["/Workspace/Stage1", "/Workspace/Coins/Coin@2"]}
            if f["op"] == "transforms":
                return {"objects": {o: {"location": {"X": 28, "Y": 0, "Z": 0}} for o in f["args"]["objects"]}}
            if f["op"] == "key":
                return {}
            if f["op"] == "ui_act":
                return ("a Roblox plugin can't click GUI buttons", 501)
            return (f"unknown op {f['op']}", 400)

        t2 = threading.Thread(target=server.serve, args=(server_answers, stop), daemon=True)
        t2.start()
        game = Game(link)
        assert game.engine.name == "roblox"
        assert game.all_actors() == ["/Workspace/Stage1", "/Workspace/Coins/Coin@2"]
        assert game.loc("/Workspace/Coins/Coin@2") == Vec(28, 0, 0)
        assert link.request_at("find", {"class": "Part"}, timeout=10)[1] == 1.5  # (the answer's game time)
        # (a session status is the bridge's own while a play test runs)
        assert link.request("session", {"action": "status"}, timeout=10) == {"running": True}
        with pytest.raises(GameError, match="can't click"):
            game.ui_act(id="Menu.Play")
        game.press("E")  # (posted: answered without the brain waiting)
        assert _until(lambda: ("key", {"key": "E", "mode": "press"}) in seen)

        # Events come in on any channel
        server.exchange([{"type": "events", "t": 3.0, "events": [
            {"kind": "heartbeat", "t": 3.0, "fps": 60},
            {"kind": "log", "t": 3.0, "verbosity": "Error", "category": "server", "message": "boom"},
            {"kind": "finding", "t": 3.0, "finding": {"detector": "log_error", "summary": "boom",
                                                     "signature": "log_error:boom"}}]}])
        assert link.logs[-1]["message"] == "boom" and link.findings[-1]["detector"] == "log_error"
        assert link.game_time == 3.0

        # The dock's frames reach the brain's messages
        edit.exchange([{"type": "start", "run": {"kind": "auto", "prompt": "Collect a coin"}}])
        msg = link.next_message(timeout=5)
        while msg and msg.get("type") == "hello":
            msg = link.next_message(timeout=5)
        assert msg == {"type": "start", "run": {"kind": "auto", "prompt": "Collect a coin"}}

        # The play test ends: its server says bye; the game's requests are 409 again
        server.post("bye", {"session": link.session, "context": "server", "why": "the play test ended"})
        with pytest.raises(bridge.BridgeError) as e:
            link.request("find", {}, timeout=10)
        assert e.value.status == 409
    finally:
        stop.set()
    # The edit plugin says bye: the link closes
    edit.post("bye", {"session": link.session, "context": "edit"})
    assert link.closed
    assert edit.post("exchange", {"session": link.session, "context": "edit"}).status_code == 404


def _until(check, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return False


def test_restart_stops_the_play_test_and_starts_another(rbx):
    edit = FakePlugin(rbx.url)
    edit.hello()
    link = rbx.links[0]
    stop = threading.Event()
    log = []
    servers = []

    def new_server():
        server = FakePlugin(rbx.url, context="server", session=link.session)
        server.attach()
        servers.append(server)

        def answers(f):
            log.append(("server", f["op"], f["args"].get("action")))
            if f["op"] == "session" and f["args"].get("action") == "stop":
                # (StudioTestService:EndTest: the play test's server goes away a moment later)
                threading.Timer(0.2, lambda: server.post("bye", {"session": link.session,
                                                                  "context": "server"})).start()
                return {"running": False}
            return {}

        threading.Thread(target=server.serve, args=(answers, stop), daemon=True).start()

    def edit_answers(f):
        log.append(("edit", f["op"], f["args"].get("action")))
        if f["op"] == "session" and f["args"].get("map"):  # (as EditHost.luau: one place, no other map)
            return ("Roblox Studio plays the open place (Obby) only: no map Other", 400)
        if f["op"] == "session" and f["args"].get("action") == "start":
            threading.Timer(0.2, new_server).start()  # (ExecutePlayModeAsync: the new test's server attaches)
            return {"running": True, "loading": True}
        return {}

    threading.Thread(target=edit.serve, args=(edit_answers, stop), daemon=True).start()
    try:
        new_server()
        assert link.request("session", {"action": "restart"}, timeout=30) == {"running": True, "restarted": True}
        assert ("server", "session", "stop") in log and ("edit", "session", "start") in log
        assert log.index(("server", "session", "stop")) < log.index(("edit", "session", "start"))
        assert _until(lambda: len(servers) == 2)
        with pytest.raises(bridge.BridgeError) as e:
            link.request("session", {"action": "restart", "map": "Other"}, timeout=30)
        assert e.value.status == 400 and "no map Other" in e.value.error
    finally:
        stop.set()


def test_serve_local_hands_out_a_roblox_link():
    """serve_local's Roblox front door: a Roblox plugin's hello returns a Link the way a WebSocket plugin's does."""
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    out = {}

    def plugin():
        url = f"http://127.0.0.1:{port}{roblox_bridge.PREFIX}"
        assert _until(lambda: roblox_bridge.ping(url, timeout=0.2) is not None, 10)
        p = FakePlugin(url, token=roblox_bridge.token())
        out["welcome"] = p.hello()

    t = threading.Thread(target=plugin, daemon=True)
    t.start()
    link = bridge.serve_local(port=0, wait=15, roblox_port=port)
    try:
        t.join(10)
        assert link.hello["engine"] == "roblox" and link.session == out["welcome"]["session"]
        assert link.server_state["current"] is link and link.roblox.port == port
    finally:
        link.server.shutdown()
        link.roblox.shutdown()


# -- the plugin's source -----------------------------------------------------------------------------------------

def test_the_plugin_answers_every_op_the_brain_needs():
    """Every op the brain requires of a Roblox plugin is answered by one of its halves (or by the bridge)."""
    edit, server, agent = _src("EditHost.luau"), _src("PlayServer.luau"), _src("ClientAgent.luau")
    answered = set(re.findall(r'op == "(\w+)"', edit + server + agent))
    needed = set(bridge.required_ops({"engine": "roblox"})) | {"codex_manifest", "codex_read"}
    host = roblox_bridge.HOST_OPS
    assert needed - answered - host == set()
    # what the bridge sends the client (when attached) is what the client agent answers, and the server relays
    client = set(re.findall(r'op == "(\w+)"', agent))
    assert roblox_bridge.CLIENT_OPS <= client
    relayed = set(re.findall(r"(\w+) = true", server[server.index("CLIENT_OPS"):server.index("}", server.index(
        "CLIENT_OPS"))]))
    assert relayed == roblox_bridge.CLIENT_OPS


def test_the_plugin_and_the_brain_agree():
    config = _src("Config.luau")
    assert f'"http://127.0.0.1:{roblox_bridge.PORT}{roblox_bridge.PREFIX}"' in config
    assert 'TOKEN = "__AUTOPILOT_ROBLOX_TOKEN__"' in config and roblox_tools.TOKEN_PLACEHOLDER in config
    assert re.search(r"Frame\.STUD_CM = (\d+)", _src("Frame.luau")).group(1) == str(int(robloxmod.STUD_CM))
    assert '"X-Autopilot-Token"' in _src("Link.luau") and roblox_bridge.TOKEN_HEADER == "X-Autopilot-Token"
    for endpoint in ("hello", "attach", "exchange", "bye"):
        assert f'"{endpoint}"' in _src("Link.luau"), endpoint
    assert re.search(r"POLL_WAIT = (\d+)", config).group(1) and int(
        re.search(r"POLL_WAIT = (\d+)", config).group(1)) <= roblox_bridge.MAX_WAIT
    # (the plugin's codex paths are the engine's)
    assert '"AutopilotCodex/"' in _src("Codex.luau") and robloxmod.EXPORT_PREFIX == "AutopilotCodex/"
    for cls, ext in robloxmod.SCRIPT_CLASSES.items():
        assert f'{cls} = "{ext}"' in _src("Codex.luau"), cls


def test_the_client_agent_carries_what_it_requires():
    """The client agent runs in the place with only Config.AGENT_MODULES under it: each of those may require only the
    others (never Config: the token stays in the plugin)."""
    agent_modules = re.findall(r'"(\w+)"', re.search(r"AGENT_MODULES = \{([^}]*)\}", _src("Config.luau")).group(1))
    assert "Config" not in agent_modules
    for name in agent_modules:
        uses = set(re.findall(r"require\(script\.Parent\.(\w+)\)", _src(f"{name}.luau")))
        assert uses <= set(agent_modules), (name, uses - set(agent_modules))
    used = set(re.findall(r'script:WaitForChild\("(\w+)"\)', _src("ClientAgent.luau")))
    assert used <= set(agent_modules)


def test_the_plugin_has_no_game_names():
    """Like the brain, the plugin learns each game at run time."""
    for f in os.listdir(SRC):
        text = _src(f)
        for word in ("Obby", "Tycoon", "Lava"):
            assert word not in text or f == "Dock.luau", (f, word)  # (the dock's placeholder is an example)


def test_what_the_game_said_before_anyone_listened_is_kept_for_the_episode():
    # (a Roblox game's scripts run as the play test starts, before an episode's runtime listens: its errors then are
    # kept in the link's backlog, which Runtime.run takes)
    link = bridge.Link(lambda frame: None)
    early = {"kind": "log", "verbosity": "Error", "category": "server", "message": "SignText:3: attempt to index nil"}
    link.feed({"type": "events", "events": [{"kind": "session", "running": True}, early]})
    assert list(link.backlog) == [early]
    heard = []
    link.listeners.append(heard.append)
    link.feed({"type": "events", "events": [{"kind": "log", "verbosity": "Warning", "message": "later"}]})
    assert list(link.backlog) == [early] and heard[-1]["message"] == "later"
    link.listeners.clear()
    link.feed({"type": "events", "events": [{"kind": "session", "running": True}]})  # (a new play test)
    assert not link.backlog


def test_studio_mcp_finds_studio_again_after_a_restart(tmp_path):
    from autopilot import studio_mcp

    exe = tmp_path / "StudioMCP"
    exe.write_text("")
    sm = studio_mcp.StudioMCP(path=str(exe))
    studios = {"now": "old"}
    calls = []

    def call(tool, args, timeout=None):
        calls.append((tool, args.get("studio_id")))
        if tool == "list_roblox_studios":
            return [{"type": "text", "text": '{"studios": [{"id": "%s", "name": "Place1"}]}' % studios["now"]}]
        if args["studio_id"] != studios["now"]:
            raise studio_mcp.StudioMCPError("The requested `studio_id` is not connected")
        return [{"type": "image", "data": base64.b64encode(_jpeg(8, 8)).decode()}]

    sm.call = call
    assert sm.capture("Place1")[:2] == b"\xff\xd8" and sm.studio == ("old", "Place1")
    studios["now"] = "new"  # (Studio restarted: a new id for the same place)
    assert sm.capture("Place1")[:2] == b"\xff\xd8" and sm.studio == ("new", "Place1")
    assert calls[-3:] == [("screen_capture", "old"), ("list_roblox_studios", None), ("screen_capture", "new")]
