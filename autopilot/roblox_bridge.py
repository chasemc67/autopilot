"""The Roblox Studio plugin's way in (docs/roblox.md). A Studio plugin can't hold a WebSocket or listen: HttpService is
its only way out. So next to bridge.serve_local's WebSocket, the brain answers HTTP on 127.0.0.1 (port 8766 by default,
AUTOPILOT_ROBLOX_PORT) and the plugin long-polls it. Each plugin session becomes an ordinary bridge.Link: requests the
brain sends are queued for the plugin's next poll, frames the plugin posts are fed to the link. Everything above the
link (cli, agents, kit) is the same as for the other engines.

A Studio plugin runs once per DataModel, so one session has up to three channels (`context`):
- "edit": the plugin in the edit DataModel. It says hello, holds the run's dock, starts the play test and serves the
  codex. Frames that aren't requests (status, result, ...) go to it.
- "server": the plugin in the play test's server. It answers the game's requests (and passes the camera, input and GUI
  ones on to the client agent it put in the game).
- "client": a plugin in the play test's client, should Studio let it make HTTP requests (UNVERIFIED): when attached,
  it takes the camera, input and GUI requests directly.

Endpoints, under /v1/roblox (JSON bodies; every one but `ping` needs the X-Autopilot-Token header):
    GET  ping                                   -> {"ok", "protocol", "service"}
    POST hello     <the hello frame>            -> {"type": "welcome", "session", "role", "features"}
    POST attach    {"session"?, "context"}      -> {"ok", "session"}
    POST exchange  {"session", "context", "frames", "wait"} -> {"frames": [...]}
    POST bye       {"session", "context"}       -> {"ok"}
"""

import base64
import hmac
import itertools
import json
import os
import secrets
import shutil
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import bridge

PORT = 8766
PREFIX = "/v1/roblox"
SERVICE = "autopilot-roblox"
TOKEN_FILE = "roblox_token"
TOKEN_HEADER = "X-Autopilot-Token"
MAX_WAIT = 25.0  # seconds a poll waits for frames (HttpService gives up on a request after about 30)
CHANNEL_TIMEOUT = 60.0  # a channel that hasn't polled for this long is gone
MAX_BODY = 64 * 1024 * 1024
MAX_BATCH = 500  # frames per answer
MAX_QUEUE = 20000  # frames waiting for one channel
CONTEXTS = ("edit", "server", "client")
# Requests the edit plugin answers (the place's scripts are read in the edit DataModel)
EDIT_OPS = {"codex_manifest", "codex_read", "levels"}
# Requests about what the player sees and does: the client's, when a client channel is attached (else the server's,
# which passes them on to the client agent)
CLIENT_OPS = {"pose", "screen", "ui", "ui_act", "key", "input", "move", "view"}
# Requests the brain answers itself: the picture (a plugin can't read the screen)
HOST_OPS = {"capture", "record"}
NO_GAME = "No game is running"
# Where pictures come from: "studio" (default: Roblox Studio's own MCP server's screen_capture, the game viewport),
# "screen" or "1" (a screenshot of the whole screen), "0" / "off" (none)
SCREENSHOT_ENV = "AUTOPILOT_ROBLOX_SCREENSHOT"
# "0": no real input through StudioMCP (keys are only what the client agent can do semantically, no GUI clicks)
REAL_INPUT_ENV = "AUTOPILOT_ROBLOX_REAL_INPUT"
# Keys the client agent presses itself, semantically (Humanoid:Move, Jump, the camera, the equipped Tool): precise and
# timed in the game's own frames. Every other key is a real key event through StudioMCP when it's there.
SEMANTIC_KEYS = {"W", "A", "S", "D", "Up", "Down", "Left", "Right", "SpaceBar", "MouseX", "MouseY",
                 "LeftMouseButton", "RightMouseButton", "MiddleMouseButton"}
# Unreal key names that aren't Roblox KeyCode names (StudioMCP takes KeyCode names)
KEYCODES = {"SpaceBar": "Space", "Enter": "Return", "BackSpace": "Backspace", "LeftCommand": "LeftMeta",
            "RightCommand": "RightMeta", "NumPadZero": "KeypadZero", "NumPadOne": "KeypadOne",
            "NumPadTwo": "KeypadTwo", "NumPadThree": "KeypadThree", "NumPadFour": "KeypadFour",
            "NumPadFive": "KeypadFive", "NumPadSix": "KeypadSix", "NumPadSeven": "KeypadSeven",
            "NumPadEight": "KeypadEight", "NumPadNine": "KeypadNine"}
KEY_TAP_MS = 60  # a real key press: down, this long, up
MAX_RECORD_FPS = 4.0  # a StudioMCP picture takes ~0.3 s
# A picture taken while a play test starts or stops can hang in Studio, and Studio's pictures stayed broken for good
# once (checked live): none is taken from a `session` start / stop / restart until SETTLE_SECONDS after the play test's
# channel came or went (SETTLE_HOLD at most, should it never come)
SETTLE_SECONDS = 2.0
SETTLE_HOLD = 45.0


def port():
    """The bridge's port: AUTOPILOT_ROBLOX_PORT, else 8766 (0: no Roblox bridge)."""
    try:
        return int(os.environ.get("AUTOPILOT_ROBLOX_PORT", PORT))
    except ValueError:
        return PORT


def token_path():
    return bridge.brain_file(TOKEN_FILE)


def token(path=None, create=True):
    """The secret the plugin must send (X-Autopilot-Token): AUTOPILOT_ROBLOX_TOKEN, else the one kept in the user
    settings folder (Autopilot/roblox_token), made on first use. `autopilot roblox install` bakes it into the plugin.
    None when there is none and create is false."""
    env = (os.environ.get("AUTOPILOT_ROBLOX_TOKEN") or "").strip()
    if env:
        return env
    path = path or token_path()
    try:
        with open(path, encoding="utf-8") as f:
            kept = f.read().strip()
        if kept:
            return kept
    except OSError:
        pass
    if not create:
        return None
    made = secrets.token_hex(16)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # (only this user may read it)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(made)
    return made


# -- the picture (a plugin can't read the screen) ----------------------------------------------------------------

def jpeg_size(data):
    """(width, height) of a JPEG from its SOF marker, or (None, None)."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        (length,) = struct.unpack(">H", data[i + 2:i + 4])
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        i += 2 + length
    return None, None


WINDOWS_SHOT = (
    "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
    "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
    "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
    "$g=[System.Drawing.Graphics]::FromImage($i);$g.CopyFromScreen($b.Left,$b.Top,0,0,$i.Size);"
    "$i.Save($env:AUTOPILOT_SHOT,[System.Drawing.Imaging.ImageFormat]::Jpeg)"
)


def picture_mode(studio_available, env=None):
    """Where a session's pictures come from: "studio" (StudioMCP's screen_capture), "screen" (the whole screen) or
    None (no pictures), from AUTOPILOT_ROBLOX_SCREENSHOT (default: "studio" where StudioMCP is)."""
    value = ((os.environ if env is None else env).get(SCREENSHOT_ENV) or "").strip().lower()
    if value in ("0", "off", "no", "none"):
        return None
    if value in ("1", "screen"):
        return "screen"
    return "studio" if studio_available else None


def screenshot(max_dim=1920, run=subprocess.run):
    """A screenshot of this computer's whole screen as a capture answer ({"jpeg": base64, "width", "height",
    "source"}), with AUTOPILOT_ROBLOX_SCREENSHOT=screen; where it can't, a BridgeError 409 "no picture: ...".
    run: subprocess.run (the tests' stand-in)."""
    folder = tempfile.mkdtemp(prefix="autopilot-shot-")
    path = os.path.join(folder, "shot.jpg")
    try:
        if sys.platform == "darwin":
            run(["screencapture", "-x", "-t", "jpg", path], check=True, timeout=20, capture_output=True)
            if max_dim and shutil.which("sips"):
                run(["sips", "-Z", str(int(max_dim)), path], check=False, timeout=20, capture_output=True)
        elif sys.platform == "win32":
            run(["powershell", "-NoProfile", "-NonInteractive", "-Command", WINDOWS_SHOT], check=True, timeout=30,
                capture_output=True, env={**os.environ, "AUTOPILOT_SHOT": path})
        else:
            raise bridge.BridgeError(409, f"no picture: no screenshot tool for {sys.platform}")
        with open(path, "rb") as f:
            data = f.read()
    except (OSError, subprocess.SubprocessError) as e:
        raise bridge.BridgeError(409, f"no picture: the screenshot failed ({e})") from None
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if not data:
        raise bridge.BridgeError(409, "no picture: the screenshot was empty")
    width, height = jpeg_size(data)
    return {"jpeg": base64.b64encode(data).decode("ascii"), "width": width, "height": height,
            "source": "host screenshot"}


def tonumber(value):
    """value as a float, or None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# -- a session ----------------------------------------------------------------------------------------------------

class Channel:
    """One of a session's plugin instances (edit, server or client): the frames waiting for its next poll."""

    def __init__(self, context):
        self.context = context
        self.frames = deque()
        self.last_seen = time.monotonic()
        self.routed = set()  # ids of the brain's requests sent to it and not answered yet
        self.gone = False


class Session:
    """One plugin session: its bridge.Link, and its channels. The link's send() routes each frame to a channel."""

    def __init__(self, hello, screenshots=None, shoot=None, studio=None):
        self.id = f"roblox-{uuid.uuid4().hex[:12]}"
        self.cond = threading.Condition()
        self.channels = {"edit": Channel("edit")}
        self._internal = {}  # id -> [threading.Event, response frame]: requests the bridge itself sent
        self._ids = itertools.count(1)
        self.closed = False
        # StudioMCP (autopilot/studio_mcp.py): the pictures and the real input a plugin can't make
        self.studio = studio if studio is not None and getattr(studio, "available", False) else None
        if screenshots is not None:  # (the tests' choice: the whole screen or nothing)
            self.pictures = "screen" if screenshots else None
        else:
            self.pictures = picture_mode(self.studio is not None)
        self.screenshots = self.pictures is not None
        self.real_input = self.studio is not None and os.environ.get(REAL_INPUT_ENV, "").strip() != "0"
        self.shoot = shoot or screenshot
        self.held = {}  # real keys held down (StudioMCP): key -> its KeyCode name, when it's let go (monotonic)
        self.lease = 1.0
        self._ticker = None
        self._recorder = None  # (thread, stop event, counts) while recording
        self._settle_at = 0.0  # (monotonic) no picture before this: a play test starting or stopping
        hello = dict(hello)
        if self.pictures:  # (the brain takes stills itself: the plugin can't)
            hello["features"] = sorted(set(hello.get("features") or []) | {"media"})
        self.link = bridge.Link(self._send)
        self.link.session = self.id
        self.link.bound = object()
        self.link.feed(hello)

    # -- the brain's frames out ------------------------------------------------------------------------------

    def play(self, op=None):
        """The channel the game's requests go to now (None: no play test is running)."""
        with self.cond:
            if op in CLIENT_OPS and self._live("client"):
                return "client"
            if self._live("server"):
                return "server"
            if self._live("client"):
                return "client"
        return None

    def _live(self, context):
        ch = self.channels.get(context)
        return ch is not None and not ch.gone

    def target(self, op, args):
        """Where a request goes: a channel's name, "host" (the bridge answers it), or None (no game: 409)."""
        if op in HOST_OPS:
            return "host"
        if op in EDIT_OPS:
            return "edit"
        play = self.play(op)
        if op == "session":
            action = str((args or {}).get("action") or "status")
            if action == "restart" or (play and action in ("status", "start")):
                return "host"
            if action == "stop":
                return play or "edit"
            return "edit"
        if play and self.real_input and self._real(op, args):
            return "host"
        if play:
            return play
        if op in ("context", "ping", "detectors", "pause", "timestep"):
            return "edit"  # (the edit plugin answers these without a game)
        return None

    def _send(self, frame):
        if self.closed:
            raise ConnectionError("the Roblox plugin is gone")
        if frame.get("type") != "request":
            self._queue("edit", frame)
            return
        op, args = str(frame.get("op") or ""), frame.get("args") or {}
        if op == "session" and str(args.get("action") or "status") in ("start", "stop", "restart"):
            self._settle_at = time.monotonic() + SETTLE_HOLD  # (until the play test's channel comes or goes)
        where = self.target(op, args)
        if where == "host":
            threading.Thread(target=self._host_op, args=(frame,), daemon=True).start()
        elif where is None:
            self._answer(frame.get("id"), error=f"{NO_GAME} (press Start in the Autopilot dock, or Play)", status=409)
        else:
            with self.cond:
                self.channels[where].routed.add(frame.get("id"))
            self._queue(where, frame)

    def _queue(self, context, frame):
        with self.cond:
            ch = self.channels.get(context)
            if ch is None or ch.gone:
                ch = self.channels["edit"]
            if len(ch.frames) >= MAX_QUEUE:
                raise ConnectionError(f"the Roblox plugin isn't polling ({len(ch.frames)} frames waiting)")
            ch.frames.append(frame)
            self.cond.notify_all()

    def _answer(self, rid, result=None, error=None, status=400):
        """Feed the link an answer to one of its requests, made here."""
        if error is not None:
            frame = {"type": "response", "id": rid, "ok": False, "status": status, "error": error}
        else:
            frame = {"type": "response", "id": rid, "ok": True, "result": result if result is not None else {}}
        self.link.feed(frame)

    # -- requests the bridge answers -------------------------------------------------------------------------

    def call(self, context, op, args=None, timeout=30.0):
        """A request of the bridge's own to a channel; its result (BridgeError for an error answer, TimeoutError when
        none comes)."""
        rid = f"rbx-{next(self._ids)}"
        entry = [threading.Event(), None]
        with self.cond:
            self._internal[rid] = entry
        try:
            self._queue(context, {"type": "request", "id": rid, "op": op, "args": args or {}})
            if not entry[0].wait(timeout):
                raise TimeoutError(f"the Roblox plugin ({context}) didn't answer {op} within {timeout:.0f}s")
        finally:
            with self.cond:
                self._internal.pop(rid, None)
        frame = entry[1] or {}
        if not frame.get("ok"):
            raise bridge.BridgeError(frame.get("status", 400), frame.get("error", "error"))
        return frame.get("result")

    def wait_for(self, check, timeout):
        """Wait until check() (called under the lock) is true; whether it became so."""
        deadline = time.monotonic() + timeout
        with self.cond:
            while not check():
                left = deadline - time.monotonic()
                if left <= 0 or self.closed:
                    return check()
                self.cond.wait(min(left, 0.5))
            return True

    def _host_op(self, frame):
        rid, op, args = frame.get("id"), frame.get("op"), frame.get("args") or {}
        try:
            if op == "capture":
                self._answer(rid, self.picture(int(args.get("max_dim") or 1920)))
            elif op == "record":
                self._answer(rid, self._record(args))
            elif op == "session":
                self._answer(rid, self._session(args))
            elif op == "key":
                self._answer(rid, self._key(args))
            elif op == "ui_act":
                self._answer(rid, self._ui_act(args))
            elif op == "input":
                self._answer(rid, self._input(args))
            else:
                self._answer(rid, error=f"unknown op {op}")
        except bridge.BridgeError as e:
            self._answer(rid, error=e.error, status=e.status)
        except Exception as e:  # noqa: BLE001 - the brain gets the error as an answer
            self._answer(rid, error=f"{type(e).__name__}: {e}", status=500)

    # -- pictures: StudioMCP's screen_capture (or the whole screen) --------------------------------------------

    def project(self):
        return str((self.link.hello or {}).get("project") or "")

    def picture(self, max_dim=1920):
        """A capture answer: {"jpeg": base64, "width", "height", "source"}; BridgeError 409 when there's none."""
        if self.pictures == "screen":
            return self.shoot(max_dim)
        if self.pictures != "studio":
            raise bridge.BridgeError(409, "no picture: a Roblox plugin can't read the screen, and Roblox Studio's MCP "
                                          f"server (StudioMCP) isn't in use ({SCREENSHOT_ENV}=screen takes "
                                          "screenshots of the whole screen instead)")
        from .studio_mcp import StudioMCPError

        deadline = time.monotonic() + 15.0
        while time.monotonic() < self._settle_at:
            if time.monotonic() >= deadline or self.closed:
                raise bridge.BridgeError(409, "no picture: the play test is starting or stopping")
            time.sleep(0.1)
        try:
            data = self.studio.capture(self.project())
        except StudioMCPError as e:
            raise bridge.BridgeError(409, f"no picture: {e}") from None
        width, height = jpeg_size(data)
        return {"jpeg": base64.b64encode(data).decode("ascii"), "width": width, "height": height,
                "source": "Roblox Studio (StudioMCP screen_capture)"}

    def _record(self, args):
        """`record`: a stream of `frame`s made here, a picture every 1/fps s (at most MAX_RECORD_FPS)."""
        self._stop_recording()
        if args.get("action") == "stop":
            return {"recording": False}
        if not self.pictures:
            raise bridge.BridgeError(409, "no picture: nothing here can take the game's picture, so it can't be "
                                          "recorded")
        self.picture(int(args.get("max_dim") or 1920))  # (fails now, not in the recorder, when there's no picture)
        fps = max(0.5, min(float(args.get("fps") or 2), MAX_RECORD_FPS))
        stop = threading.Event()
        thread = threading.Thread(target=self._recording, args=(stop, fps, int(args.get("max_dim") or 1920)),
                                  daemon=True, name="roblox-record")
        self._recorder = (thread, stop)
        thread.start()
        return {"recording": True, "fps": fps, "source": "host"}

    def _stop_recording(self):
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            recorder[1].set()

    def _recording(self, stop, fps, max_dim):
        n, misses = 0, 0
        while not stop.is_set() and not self.closed:
            if self.pictures == "studio" and time.monotonic() < self._settle_at:
                stop.wait(0.1)  # (the play test is starting or stopping: no frame now)
                continue
            started = time.monotonic()
            try:
                shot = self.picture(max_dim)
                misses = 0
            except Exception:  # noqa: BLE001 - a missed frame (Studio busy): the next one is tried
                misses += 1
                stop.wait(min(5.0, misses / fps))
                continue
            n += 1
            game_time = getattr(self.link, "game_time", None)
            at = getattr(self.link, "game_time_at", None)
            t = round(game_time + (time.time() - at), 2) if isinstance(game_time, (int, float)) and at else None
            self.link.feed({"type": "frame", "n": n, "t": t, "wall": time.time(), **shot})
            stop.wait(max(0.0, 1.0 / fps - (time.monotonic() - started)))

    # -- real input: StudioMCP's keyboard and mouse --------------------------------------------------------------

    def _real(self, op, args):
        """Whether a request is answered here with real input (else the play test's plugin answers it)."""
        if op in ("ui_act", "input"):
            return True
        if op == "key":
            mode = str(args.get("mode") or "press")
            key = str(args.get("key") or "")
            return mode == "release_all" or (key != "" and key not in SEMANTIC_KEYS)
        return False

    def _play_call(self, op, args, timeout=30.0):
        where = self.play(op)
        if where is None:
            raise bridge.BridgeError(409, f"{NO_GAME} (press Start in the Autopilot dock, or Play)")
        return self.call(where, op, args, timeout=timeout)

    def _keys(self, actions):
        self.studio.keys(self.project(), actions)

    def _key(self, args):
        """`key` with a real key event (StudioMCP), for the keys the client agent can't press for real: anything bound
        through ContextActionService or read from UserInputService, ProximityPrompts, the backpack's hotkeys. A key
        StudioMCP doesn't take goes to the client agent, which does what it can."""
        from .studio_mcp import StudioMCPError

        mode = str(args.get("mode") or "press")
        if mode == "release_all":
            self._release(list(self.held))
            return self._play_call("key", args)
        key = str(args.get("key") or "")
        code = KEYCODES.get(key, key)
        if tonumber(args.get("lease")):
            self.lease = min(10.0, max(0.2, float(args["lease"])))
        try:
            if mode == "release":
                self._release([key])
            elif mode == "hold":
                seconds = tonumber(args.get("seconds")) or 0
                with self.cond:
                    down = key in self.held
                    self.held[key] = (code, time.monotonic() + (seconds if seconds > 0 else self.lease))
                if not down:
                    self._keys([{"action": "keyDown", "key_code": code}])
                self._tick()
            else:
                self._keys([{"action": "keyDown", "key_code": code}, {"action": "wait", "wait_time_ms": KEY_TAP_MS},
                            {"action": "keyUp", "key_code": code}])
                return {"did": f"real key {code}"}
        except StudioMCPError as e:
            with self.cond:
                self.held.pop(key, None)
            answer = self._play_call("key", args)
            return {**(answer or {}), "note": f"no real key ({e}): {(answer or {}).get('note') or 'done in the game'}"}
        return {}

    def _release(self, keys):
        from .studio_mcp import StudioMCPError

        with self.cond:
            codes = [self.held.pop(k)[0] for k in keys if k in self.held]
        for code in codes:
            try:
                self._keys([{"action": "keyUp", "key_code": code}])
            except StudioMCPError:
                pass

    def _tick(self):
        """Let go of the real keys whose hold ran out (a lease not renewed, or a timed hold's seconds)."""
        if self._ticker is not None and self._ticker.is_alive():
            return

        def run():
            while not self.closed:
                with self.cond:
                    if not self.held:
                        self._ticker = None
                        return
                    now = time.monotonic()
                    done = [k for k, (_, until) in self.held.items() if until <= now]
                if done:
                    self._release(done)
                time.sleep(0.05)

        self._ticker = threading.Thread(target=run, daemon=True, name="roblox-keys")
        self._ticker.start()

    def _ui_act(self, args):
        """`ui_act`: a click is a real mouse click (StudioMCP) on the element the client agent names; the rest is the
        client agent's."""
        from .studio_mcp import StudioMCPError

        if str(args.get("action") or "click") != "click":
            return self._play_call("ui_act", args)
        found = self._play_call("ui_act", {**args, "host_input": True}) or {}
        path = found.get("click_path")
        if not path:
            return found
        try:
            self.studio.mouse(self.project(), [{"action": "moveTo", "instance_path": path},
                                               {"action": "mouseButtonClick", "mouse_button": "left"}])
        except StudioMCPError as e:
            raise bridge.BridgeError(502, f"the click on {args.get('id')} didn't happen: {e}") from None
        return {"clicked": str(args.get("id") or path), "how": "a real mouse click (Roblox Studio's MCP server)"}

    def _input(self, args):
        """`input`: a ContextActionService action is pressed by its key, for real (Roblox doesn't let a script call
        an action's function); a ProximityPrompt is the client agent's."""
        from .studio_mcp import StudioMCPError

        found = self._play_call("input", {**args, "host_input": True}) or {}
        keys = found.get("host_keys") or []
        if not keys:
            return found
        code, mode = str(keys[0]), str(args.get("mode") or "press")
        try:
            if mode == "hold":
                with self.cond:
                    self.held[f"action:{args.get('action')}"] = (code, float("inf"))
                self._keys([{"action": "keyDown", "key_code": code}])
            elif mode == "release":
                self._release([f"action:{args.get('action')}"])
            else:
                self._keys([{"action": "keyDown", "key_code": code}, {"action": "wait", "wait_time_ms": KEY_TAP_MS},
                            {"action": "keyUp", "key_code": code}])
        except StudioMCPError as e:
            raise bridge.BridgeError(502, f"the action {args.get('action')} wasn't pressed: {e}") from None
        return {"did": f"real key {code} (action {args.get('action')})"}

    def _session(self, args):
        """`session` while a play test runs (status, start) and `restart`: stop the play test (the server's
        StudioTestService:EndTest), wait for it to end, start a new one (the edit plugin), and wait for its server."""
        action = str(args.get("action") or "status")
        if action in ("status", "start") and self.play():
            return {"running": True}
        if self.play():
            try:
                self.call(self.play(), "session", {"action": "stop"}, timeout=30)
            except bridge.BridgeError:
                pass
            if not self.wait_for(lambda: not self._live("server") and not self._live("client"), 30):
                raise bridge.BridgeError(500, "The play test didn't stop within 30 s")
        start = {"action": "start"}
        if args.get("map"):
            start["map"] = args["map"]
        self.call("edit", "session", start, timeout=30)
        if not self.wait_for(lambda: self._live("server") or self._live("client"), 45):
            raise bridge.BridgeError(500, "The play test started but its server didn't reach Autopilot within 45 s "
                                          "(is Allow HTTP Requests on in Game Settings > Security?)")
        return {"running": True, "restarted": action == "restart"}

    # -- the plugin's frames in ------------------------------------------------------------------------------

    def attach(self, context):
        """A channel joins (again). A play channel that joins while one of its kind is there replaces it."""
        if context != "edit":
            self._settle_at = time.monotonic() + SETTLE_SECONDS
        replaced, failed = None, []
        with self.cond:
            old = self.channels.get(context)
            if context == "edit" and old is not None and not old.gone:
                old.last_seen = time.monotonic()
            else:
                if old is not None and not old.gone:
                    replaced, failed = old, self._drop(old)
                self.channels[context] = Channel(context)
            self.cond.notify_all()
        if replaced is not None:
            self._fail(replaced, failed, "replaced by a new play test")

    def incoming(self, context, frame):
        if not isinstance(frame, dict):
            return
        rid = frame.get("id")
        if frame.get("type") == "response":
            with self.cond:
                entry = self._internal.get(rid)
                ch = self.channels.get(context)
                if ch is not None:
                    ch.routed.discard(rid)
            if entry is not None:
                entry[1] = frame
                entry[0].set()
                return
        self.link.feed(frame)

    def take(self, context, wait):
        """The frames waiting for a channel, waiting up to `wait` seconds for some."""
        deadline = time.monotonic() + max(0.0, min(float(wait or 0), MAX_WAIT))
        with self.cond:
            ch = self.channels.get(context)
            while ch is not None and not ch.frames and not ch.gone and not self.closed:
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self.cond.wait(left)
                ch = self.channels.get(context)
            if ch is None:
                return []
            ch.last_seen = time.monotonic()
            out = []
            while ch.frames and len(out) < MAX_BATCH:
                out.append(ch.frames.popleft())
            return out

    def detach(self, context, why):
        with self.cond:
            ch = self.channels.get(context)
            if ch is None or ch.gone:
                return
            failed = self._drop(ch)
        self._fail(ch, failed, why)
        if context == "edit":
            self.close(why)

    def _drop(self, ch):
        """A channel ends (under the lock): the ids of the brain's requests it held, unanswered (see _fail)."""
        ch.gone = True
        if ch.context != "edit":
            self._settle_at = time.monotonic() + SETTLE_SECONDS
        routed, ch.routed = list(ch.routed), set()
        waiting = [f for f in ch.frames if f.get("type") == "request"]
        ch.frames.clear()
        if ch.context != "edit":  # (what was queued for the play test goes nowhere: it ended)
            routed += [f.get("id") for f in waiting if f.get("id") not in routed]
        self.cond.notify_all()
        return routed

    def _fail(self, ch, routed, why):
        """Answer the requests a channel that ended held with an error (outside the lock: feeding the link may send)."""
        for rid in routed:
            with self.cond:
                entry = self._internal.get(rid)
            if entry is not None:
                entry[1] = {"ok": False, "status": 409, "error": f"{NO_GAME} ({why})"}
                entry[0].set()
            else:
                self._answer(rid, error=f"{NO_GAME} ({why})", status=409)
        if ch.context != "edit":
            with self.cond:
                self.held.clear()  # (the keys were down in the play test that ended)
            self.link.feed({"type": "events", "events": [{"kind": "session", "running": False, "why": why}]})

    def reap(self, now=None):
        """Drop the channels that stopped polling."""
        now = time.monotonic() if now is None else now
        with self.cond:
            stale = [c for c, ch in self.channels.items() if not ch.gone and now - ch.last_seen > CHANNEL_TIMEOUT]
        for context in sorted(stale, key=lambda c: c == "edit"):  # (the play test's first: the edit's closes all)
            self.detach(context, f"the Roblox plugin ({context}) stopped polling")

    def close(self, why):
        self._stop_recording()
        if self.held and self.real_input:
            self._release(list(self.held))
        with self.cond:
            self.closed = True
            self.cond.notify_all()
        self.link.close(why)


# -- the HTTP server ----------------------------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # (HTTPServer's own asks socket.getfqdn() for the server's name, which can stall for half a minute on a Mac
        # whose DNS doesn't answer for its own address: the name is never used here)
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port


class RobloxBridge:
    """The HTTP front door for Roblox plugins. on_link(link) is called with each new session's Link (serve_local
    hands it out as it does a WebSocket plugin's)."""

    def __init__(self, on_link, host="127.0.0.1", port=PORT, secret=None, screenshots=None, shoot=None,
                 studio="auto"):
        """studio: a studio_mcp.StudioMCP for pictures and real input ("auto": Studio's own, where it's installed;
        None: none)."""
        if studio == "auto":
            from .studio_mcp import StudioMCP

            studio = StudioMCP()
        self.studio = studio
        self.on_link = on_link
        self.secret = secret if secret is not None else token()
        self.sessions = {}
        self.latest = None  # the newest session (a play test that doesn't name its session joins it)
        self.screenshots, self.shoot = screenshots, shoot
        self._lock = threading.Lock()
        bridge_self = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # (quiet: the plugin polls all the time)
                pass

            def do_GET(self):
                bridge_self._handle(self, "GET")

            def do_POST(self):
                bridge_self._handle(self, "POST")

        self.server = _Server((host, port), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://{host}:{self.port}{PREFIX}"
        self._stop = threading.Event()
        threading.Thread(target=self.server.serve_forever, daemon=True, name="roblox-bridge").start()
        threading.Thread(target=self._reaper, daemon=True, name="roblox-reaper").start()

    def shutdown(self):
        self._stop.set()
        self.server.shutdown()
        self.server.server_close()
        for s in list(self.sessions.values()):
            s.close("the agent service stopped")
        if self.studio is not None:
            self.studio.close()

    def _reaper(self):
        while not self._stop.wait(5.0):
            for s in list(self.sessions.values()):
                s.reap()
                if s.closed:
                    with self._lock:
                        self.sessions.pop(s.id, None)

    # -- one request ----------------------------------------------------------------------------------------

    def _handle(self, h, method):
        path = urlparse(h.path).path.rstrip("/")
        if not path.startswith(PREFIX):
            return self._reply(h, 404, {"error": "not found"})
        name = path[len(PREFIX):].lstrip("/")
        if method == "GET" and name == "ping":
            return self._reply(h, 200, {"ok": True, "protocol": bridge.PROTOCOL, "service": SERVICE})
        if method != "POST":
            return self._reply(h, 405, {"error": "POST only"})
        if not bridge.is_loopback(h.client_address):
            return self._reply(h, 403, {"error": "only this computer may connect"})
        given = str(h.headers.get(TOKEN_HEADER) or "")
        if not self.secret or not hmac.compare_digest(given.encode(), str(self.secret).encode()):
            return self._reply(h, 401, {"error": "token rejected: run `autopilot roblox status --show-token` and "
                                                 "paste the token into the Autopilot dock"})
        try:
            size = int(h.headers.get("Content-Length") or 0)
            if size > MAX_BODY:
                return self._reply(h, 413, {"error": "too big"})
            body = json.loads(h.rfile.read(size) or b"{}") if size else {}
            if not isinstance(body, dict):
                raise ValueError("not an object")
        except ValueError as e:
            return self._reply(h, 400, {"error": f"bad JSON: {e}"})
        handler = {"hello": self._hello, "attach": self._attach, "exchange": self._exchange, "bye": self._bye}.get(name)
        if handler is None:
            return self._reply(h, 404, {"error": f"no endpoint {name}"})
        status, answer = handler(body, h)
        return self._reply(h, status, answer)

    def _reply(self, h, status, body):
        data = json.dumps(body).encode("utf-8")
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def _session(self, body):
        sid = body.get("session")
        with self._lock:
            s = self.sessions.get(sid) if sid else self.latest
        return s if s is not None and not s.closed else None

    def _hello(self, body, h):
        if body.get("type") != "hello" or body.get("protocol") != bridge.PROTOCOL:
            return 400, {"type": "error", "message": f"expected hello with protocol {bridge.PROTOCOL}"}
        resumed = self._session({"session": body.get("resume")}) if body.get("resume") else None
        if resumed is not None:
            resumed.attach("edit")
            resumed.link.hello = {**resumed.link.hello, **body}
            if resumed.pictures:
                resumed.link.hello["features"] = sorted(set(body.get("features") or []) | {"media"})
            resumed.link.last_heartbeat = time.time()
            s = resumed
        else:
            body = {**body, "engine": body.get("engine") or "roblox"}
            s = Session(body, self.screenshots, self.shoot, self.studio)
            s.link.loopback = True
            s.link.auth_token, s.link.mode = _auth(h.headers)
            with self._lock:
                self.sessions[s.id] = s
                self.latest = s
            self.on_link(s.link)
        return 200, {"type": "welcome", "role": "service", "session": s.id, "features": bridge.BRAIN_FEATURES,
                     "resumed": resumed is not None}

    def _attach(self, body, h):
        context = body.get("context")
        if context not in ("server", "client"):
            return 400, {"error": "context must be server or client"}
        # (a session this bridge doesn't know: the agent service restarted while the play test ran, and the edit
        # plugin said hello again into a new one: the play test joins that, the newest)
        s = self._session(body) or self._session({})
        if s is None:
            return 404, {"error": "no Autopilot session: open the Autopilot dock in the edit window first"}
        s.attach(context)
        return 200, {"ok": True, "session": s.id}

    def _exchange(self, body, h):
        context = body.get("context") or "edit"
        s = self._session(body) if body.get("session") else None
        if s is None:
            return 404, {"error": "unknown session (say hello again)"}
        ch = s.channels.get(context)
        if ch is None or ch.gone:
            return 409, {"error": f"the {context} channel isn't attached"}
        ch.last_seen = time.monotonic()
        for frame in body.get("frames") or []:
            s.incoming(context, frame)
        return 200, {"frames": s.take(context, body.get("wait") or 0)}

    def _bye(self, body, h):
        s = self._session(body) if body.get("session") else None
        if s is not None:
            s.detach(body.get("context") or "edit", str(body.get("why") or "the plugin said goodbye"))
        return 200, {"ok": True}


def _auth(headers):
    auth = str(headers.get("Authorization") or "").strip()
    bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    mode = str(headers.get("X-Autopilot-Mode") or "").strip().lower()
    return bearer or None, mode or None


def ping(url=None, timeout=2.0):
    """The bridge's ping answer at url (default: this machine's), or None when nothing answers."""
    import requests

    url = url or f"http://127.0.0.1:{port()}{PREFIX}"
    try:
        r = requests.get(f"{url}/ping", timeout=timeout)
        return r.json() if r.ok else None
    except (requests.RequestException, ValueError):
        return None
