"""Roblox Studio's own MCP server (StudioMCP, shipped inside Studio) as the Roblox bridge's eyes and real hands
(docs/roblox.md, "Pictures and real input"). A Studio plugin can't read the screen or make input events; StudioMCP can:
`screen_capture` gives the game viewport as a JPEG (also with Studio in the background), `user_keyboard_input` and
`user_mouse_input` make real key and mouse events in the play test's client (ContextActionService bindings,
UserInputService, GUI buttons see them). The bridge starts one StudioMCP (stdio, JSON-RPC) and keeps it.

    AUTOPILOT_ROBLOX_STUDIO_MCP   the StudioMCP executable (default: inside Studio's app bundle / install folder;
                                  "0" turns StudioMCP off: then no pictures but a whole-screen screenshot, no real input)
"""

import base64
import glob
import json
import os
import subprocess
import sys
import threading
import time

ENV = "AUTOPILOT_ROBLOX_STUDIO_MCP"
PROTOCOL = "2025-06-18"
CALL_TIMEOUT = 20.0


class StudioMCPError(Exception):
    pass


def find(env=None):
    """The StudioMCP executable, or None (not installed, or turned off)."""
    env = os.environ if env is None else env
    given = (env.get(ENV) or "").strip()
    if given in ("0", "off", "false"):
        return None
    if given:
        return given if os.path.isfile(given) else None
    if sys.platform == "darwin":
        for app in ("/Applications/RobloxStudio.app", os.path.expanduser("~/Applications/RobloxStudio.app")):
            path = os.path.join(app, "Contents", "MacOS", "StudioMCP")
            if os.path.isfile(path):
                return path
    elif sys.platform == "win32":
        base = os.path.join(env.get("LOCALAPPDATA") or "", "Roblox", "Versions")
        found = sorted(glob.glob(os.path.join(base, "*", "StudioMCP.exe")), key=os.path.getmtime, reverse=True)
        if found:
            return found[0]
    return None


class StudioMCP:
    """One StudioMCP process, started on first use and again after it dies. Calls are serialised (one at a time)."""

    def __init__(self, path=None, popen=subprocess.Popen):
        self.path = path or find()
        self.popen = popen
        self.proc = None
        self.lock = threading.Lock()
        self._ids = 0
        self._answers = {}
        self._cond = threading.Condition()
        self.studio = None  # (id, name) of the Studio instance the calls go to
        self.problem = None

    @property
    def available(self):
        return bool(self.path)

    # -- the process ----------------------------------------------------------------------------------------------

    def _start(self):
        if self.proc is not None and self.proc.poll() is None:
            return
        if not self.path:
            raise StudioMCPError("Roblox Studio's MCP server (StudioMCP) wasn't found")
        self.proc = self.popen([self.path], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="studio-mcp-read").start()
        self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                 "clientInfo": {"name": "autopilot-roblox-bridge", "version": "1"}}, started=True)
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        self.studio = None

    def _read(self, proc):
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") is not None:
                with self._cond:
                    self._answers[msg["id"]] = msg
                    self._cond.notify_all()
        with self._cond:
            self._cond.notify_all()

    def _write(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _rpc(self, method, params, timeout=CALL_TIMEOUT, started=False):
        if not started:
            self._start()
        self._ids += 1
        rid = self._ids
        try:
            self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        except (OSError, ValueError) as e:
            self.close()
            raise StudioMCPError(f"StudioMCP went away ({e})") from None
        deadline = time.monotonic() + timeout
        with self._cond:
            while rid not in self._answers:
                left = deadline - time.monotonic()
                if left <= 0 or self.proc is None or self.proc.poll() is not None:
                    gone = self.proc is None or self.proc.poll() is not None
                    if gone:
                        self.close()
                    raise StudioMCPError(f"StudioMCP {'went away' if gone else 'did not answer'} ({method})")
                self._cond.wait(min(left, 0.5))
            msg = self._answers.pop(rid)
        if msg.get("error"):
            raise StudioMCPError(f"StudioMCP: {msg['error'].get('message') or msg['error']}")
        return msg.get("result") or {}

    def close(self):
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass

    # -- tools ----------------------------------------------------------------------------------------------------

    def call(self, tool, arguments, timeout=CALL_TIMEOUT):
        """A tool's result content (a list of {"type", ...}); StudioMCPError for an error result."""
        with self.lock:
            result = self._rpc("tools/call", {"name": tool, "arguments": arguments}, timeout)
        content = result.get("content") or []
        if result.get("isError"):
            words = " ".join(c.get("text", "") for c in content if c.get("type") == "text").strip()
            raise StudioMCPError(words or f"StudioMCP {tool} failed")
        return content

    def choose(self, name):
        """The Studio instance whose place is `name` (the plugin's hello.project): its id. Studio instances with
        other places are never used; with none of that name, the only one open."""
        if self.studio and self.studio[1] == name:
            return self.studio[0]
        content = self.call("list_roblox_studios", {})
        text = next((c.get("text") for c in content if c.get("type") == "text"), "{}")
        try:
            studios = json.loads(text).get("studios") or []
        except ValueError:
            studios = []
        named = [s for s in studios if name and str(s.get("name") or "") in (name, f"{name}.rbxl", f"{name}.rbxlx")]
        pick = named[0] if named else (studios[0] if len(studios) == 1 and not name else None)
        if pick is None:
            raise StudioMCPError(f"no Roblox Studio with the place {name!r} is connected to StudioMCP "
                                 f"(open: {', '.join(repr(s.get('name')) for s in studios) or 'none'})")
        self.studio = (pick["id"], pick.get("name"))
        return pick["id"]

    def on_studio(self, name, tool, arguments):
        """A tool's call on the Studio whose place is `name`. Studio's id changes when Studio restarts: a call that
        fails on the id kept from before is tried once more on the Studio found again."""
        kept = self.studio
        try:
            return self.call(tool, {"studio_id": self.choose(name), **arguments})
        except StudioMCPError:
            if kept is None:
                raise
            self.studio = None
            return self.call(tool, {"studio_id": self.choose(name), **arguments})

    def capture(self, name):
        """The game viewport now, as JPEG bytes."""
        content = self.on_studio(name, "screen_capture", {"capture_id": f"Autopilot_{int(time.time() * 1000)}"})
        for c in content:
            if c.get("type") == "image" and c.get("data"):
                return base64.b64decode(c["data"])
        raise StudioMCPError("StudioMCP's screen_capture gave no picture")

    def keys(self, name, actions):
        """Real keyboard input in the play test's client: [{"action": "keyDown"|"keyUp"|"keyPress"|"wait", ...}]."""
        self.on_studio(name, "user_keyboard_input", {"datamodel_type": "Client", "actions": actions})

    def mouse(self, name, actions):
        """Real mouse input in the play test's client: [{"action": "moveTo"|"mouseButtonClick"|..., ...}]."""
        self.on_studio(name, "user_mouse_input", {"datamodel_type": "Client", "actions": actions})
