import os
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Tests that time a fake game against the wall clock. Hosted macOS CI runners have coarse timers: a 50 ms time.sleep
# takes 90-210 ms there (measured 2026-10-10, with or without QoS user-interactive and an App Nap opt-out; Linux runners
# sleep 50 ms), so the runtime ticks about 7 times a second instead of 20. They run everywhere else, real Macs included.
wall_clock = pytest.mark.skipif(sys.platform == "darwin" and os.environ.get("GITHUB_ACTIONS") == "true",
                                reason="hosted macOS runners' timers are coarse (a 50 ms sleep takes 90-210 ms)")


@pytest.fixture(autouse=True)
def no_account_threads_left(monkeypatch):
    """A test's sign-in and bill threads (autopilot/account.py) end inside it, while its stand-ins are still in place.
    One left running (a sign-in thread still sending its account frame) once finished during a later test, under that
    test's stand-ins, and asked for its bill first: the later test never saw the bill come in."""
    yield
    for t in threading.enumerate():
        if t.name in ("aura-sign-in", "aura-bill"):
            t.join(10)


@pytest.fixture(autouse=True)
def no_kept_aura_session(tmp_path, monkeypatch):
    """Tests never read or write the Aura sign-in this machine keeps (autopilot/aura_login.py)."""
    from autopilot import aura_login

    path = str(tmp_path / "aura-session.json")
    monkeypatch.setattr(aura_login, "_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def no_user_settings(tmp_path, monkeypatch):
    """Tests never read this machine's Autopilot/autopilot.env (its keys, Coding Agent, provider and
    AUTOPILOT_AURA_SERVER) or inherit what it set."""
    from autopilot import account, cli

    monkeypatch.setattr(cli, "load_user_keys", lambda: None)
    monkeypatch.setattr(account, "_user_env_path", lambda: str(tmp_path / "autopilot.env"))
    monkeypatch.setattr(account, "_from_user_env", {})
    for name in ("AUTOPILOT_AURA_SERVER", "AUTOPILOT_AGENT", "AUTOPILOT_OPENCODE_PROVIDER", "AUTOPILOT_MODEL"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def no_jev_kinds(monkeypatch):
    """Tests never ask Jev the kind of a run (autopilot/cli.py classify): its first words decide, never a test. The
    real classify is the fixture's value, for a test of it."""
    from autopilot import cli

    real = cli.classify
    monkeypatch.setattr(cli, "classify", lambda prompt, client=None: (cli.kind_by_words(prompt), False))
    return real


@pytest.fixture(autouse=True)
def own_preferences(tmp_path, monkeypatch):
    """Tests read the first-run setup's choices from a file of their own (none at first), never this machine's."""
    from autopilot import account

    path = tmp_path / "preferences.json"
    monkeypatch.setattr(account, "_preferences_path", lambda: str(path))
    monkeypatch.setattr(account, "_fixed", None)
    monkeypatch.setattr(account, "_from_preferences", False)
    monkeypatch.setattr(account, "_bill", {"value": None, "at": 0.0, "server": None, "asking": False})
    return path


@pytest.fixture(autouse=True)
def no_aura_bill(monkeypatch):
    """Tests never ask Aura for the period's bill or the team's credits (autopilot/account.py): they answer what a test
    sets ({} = signed out: "credits" is fetch_credits' answer, (False, None) when Aura didn't say)."""
    from autopilot import account

    answer = {}
    monkeypatch.setattr(account, "fetch_bill", lambda link=None, http=None: answer.get("bill"))
    monkeypatch.setattr(account, "fetch_credits", lambda link=None, http=None: answer.get("credits", (False, None)))
    monkeypatch.setitem(account._bill, "credits", None)
    return answer


@pytest.fixture(autouse=True)
def no_training_uploads(monkeypatch):
    """Tests never send a run's data anywhere (autopilot/training.py): what would have been sent is listed instead."""
    from autopilot import training

    sent = []
    monkeypatch.setattr(training, "upload_later", lambda *args: sent.append(args))
    return sent


@pytest.fixture(autouse=True)
def no_retry_waits(monkeypatch):
    """Tests never wait between tries (autopilot/retry.py): the waits asked for are listed instead."""
    from autopilot import retry

    waits = []
    monkeypatch.setattr(retry, "pause", waits.append)
    return waits


@pytest.fixture(autouse=True)
def no_install_memory(monkeypatch):
    """Tests never keep a memory with Aura for this install (autopilot/aura_memory.py): install_memory answers what a
    test sets ("kept": a MemorySync), else None; "asked" lists the project keys it was asked for."""
    from autopilot import aura_memory

    answer = {"kept": None, "asked": []}

    def install_memory(project, say=None):
        answer["asked"].append(project)
        return answer["kept"]

    monkeypatch.setattr(aura_memory, "install_memory", install_memory)
    return answer


@pytest.fixture(autouse=True)
def no_real_assistants(tmp_path, monkeypatch):
    """Tests never change this machine's Claude Code or Codex (autopilot/connect.py, which `autopilot uninstall` runs
    too): their homes are folders of the test's own, and the `claude` command is never run ("ran" lists what would
    have been, answered by "answers": {command's first words: (code, output)}, else (None, "not here"))."""
    from autopilot import connect

    home = tmp_path / "assistants"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(home / "codex"))
    monkeypatch.setattr(connect, "agents_home", lambda: str(home / "agents"))
    state = {"ran": [], "answers": {}, "home": home}

    def run(cmd):
        state["ran"].append(list(cmd))
        return state["answers"].get(" ".join(cmd[:3]), (None, "not here"))

    monkeypatch.setattr(connect, "run", run)
    return state


@pytest.fixture(autouse=True)
def no_real_uv(monkeypatch):
    """Tests never uninstall this machine's `autopilot` command (autopilot/uninstall.py): uninstall(exe=None) looks uv
    up (None isn't "no uv"), and a test calling it removed the real uv tool, so here there is no uv, and any call to the
    real one fails the test. A test of uv's tools sets its own stand-ins (_uv, uv) over these."""
    from autopilot import uninstall

    monkeypatch.setattr(uninstall, "uv", lambda: None)
    monkeypatch.setattr(uninstall, "_uv", lambda exe, *args: pytest.fail(f"a test ran the real uv: {args}"))


@pytest.fixture(autouse=True)
def no_roblox_bridge(tmp_path, monkeypatch):
    """Tests never open the Roblox bridge's port beside serve_local's WebSocket, nor read or make this machine's
    Roblox token (autopilot/roblox_bridge.py): a test of the bridge starts its own."""
    from autopilot import roblox_bridge

    monkeypatch.setenv("AUTOPILOT_ROBLOX_PORT", "0")
    monkeypatch.delenv("AUTOPILOT_ROBLOX_TOKEN", raising=False)
    monkeypatch.delenv(roblox_bridge.SCREENSHOT_ENV, raising=False)
    monkeypatch.setattr(roblox_bridge, "token_path", lambda: str(tmp_path / "roblox_token"))


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    """Run in an empty directory, so files a test writes go there."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


class FakeGame:
    """Answers bridge requests the way the plugin does, for a tiny made-up world."""

    def __init__(self):
        self.props = {
            "/Game/L.L:PersistentLevel.Player_C_0": {"Health": 100, "MaxHealth": 100,
                                                     "JumpAction": "/Game/IA_Jump.IA_Jump"},
            "/Game/L.L:PersistentLevel.Player_C_0.Cargo": {"Capacity": 7, "Items": []},
        }
        self.time = 1.0
        self.paused = False
        self.running = True
        self.log = []  # (op, args) of every request

    def answer(self, op, args):
        self.log.append((op, args))
        if op == "ping":
            return {"game_time": self.time}
        if op == "context":
            return {"world": "/Game/L.L", "character": "/Game/L.L:PersistentLevel.Player_C_0",
                    "controller": "/Game/L.L:PersistentLevel.PC_0", "input_subsystem": "/Engine/Transient.EIS_0",
                    "game_name": "fakegame", "host": "game", "session_running": self.running,
                    "paused": self.paused, "game_time": self.time}
        if op == "session":
            if args.get("action") == "restart":
                self.time = 0.0
            return {"running": self.running}
        if op in ("input", "pause", "timestep", "detectors"):
            return {}
        if op == "move":
            self.time += args.get("seconds", 0)
            return {"moved": 100.0}
        if op == "rc":
            body = args["body"]
            path = body.get("objectPath")
            if args["verb"] == "property":
                if path not in self.props:
                    raise KeyError(f"Object {path} does not exist")
                if "propertyValue" in body:
                    self.props[path].update(body["propertyValue"])
                    return {}
                name = body.get("propertyName")
                return {name: self.props[path][name]} if name else dict(self.props[path])
            if args["verb"] == "call":
                if body["functionName"] == "GetTimeSeconds":
                    return {"ReturnValue": self.time}
                if body["functionName"] == "GetGameName":
                    return {"ReturnValue": "fakegame"}
                return {"ReturnValue": None}
        raise KeyError(f"unknown op {op}")


def connect(link, game, events=()):
    """Wire a Link to a FakeGame: requests are answered at once, in order, like the plugin does."""

    def send(frame):
        if frame.get("type") != "request":
            return
        try:
            result = game.answer(frame["op"], frame["args"])
            link.feed({"type": "response", "id": frame["id"], "ok": True, "result": result, "t": game.time})
        except KeyError as e:
            link.feed({"type": "response", "id": frame["id"], "ok": False, "status": 400,
                       "error": str(e).strip("'\""), "t": game.time})

    link._send = send
    for e in events:
        link.feed({"type": "events", "events": [e], "t": game.time})
    return link
