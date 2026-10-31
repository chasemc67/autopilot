"""The link between the brain and the plugin in the game (Unreal, Unity, Godot or Roblox Studio). The wire format is
in docs/protocol.md; Roblox's plugin reaches it over HTTP instead (autopilot/roblox_bridge.py, docs/roblox.md).

The plugin connects out to the brain over a WebSocket; ``serve_local()`` listens for it on this machine and returns a
``Link`` once the plugin has said hello. Through the link the brain sends requests to the game (``request``, ``post``)
and receives events (findings, logs, heartbeats) and control frames (start, stop, steer).

Errors from the game are ``BridgeError``s (an HTTP-style status and message, as the plugin sent them). A lost
connection, or a request the game doesn't answer in time, raises ``requests.ConnectionError``.
"""

import atexit
import ipaddress
import itertools
import json
import os
import queue
import sys
import threading
import time
import uuid

import requests

import autopilot

PROTOCOL = 1
TIMEOUT = 30.0  # seconds to wait for an answer from the game
DEFERRED_SLACK = 15.0  # extra seconds for requests that answer when they finish (move, watch)
# A plugin that closes with this code is coming back (Unity reloads its scripts on entering play mode): its Link waits
# RESUME_SECONDS for a hello with {"resume": <its session>} on a new connection, holding what is sent meanwhile
RELOAD_CLOSE = 1012
RESUME_SECONDS = 90.0
# A plugin started with a run on its command line (hello "run") that loses the result and connects again says the same
# hello: within this many seconds of that run, the brain answers it with the run's result (or an error), never a second
# run (autopilot/cli.py)
REPEAT_SECONDS = 180
# What this brain does that a plugin may offer only when told (the welcome's "features"): "steer", the user's
# words to a run while it plays (a `steer` frame); "usage", the tokens a run uses (`usage` frames); "account", how runs
# go and the Aura sign-in the plugin may ask for (`account`, `aura_sign_in` frames: autopilot/account.py)
BRAIN_FEATURES = ["steer", "usage", "account"]
DEFERRED_OPS = ("move", "watch")
# Ops the brain can't play without, each with arguments that make it answer without changing anything (see
# Link.missing_ops). A plugin lacking one answers "unknown op": without this check the brain would go on blind
# (no `view`) or with its keys doing nothing (no `key`), and only its verdicts would show it.
REQUIRED_OPS = {
    "context": {},
    "session": {"action": "status"},
    "ping": {},
    "rc": {"verb": "probe"},
    "screen": {},
    "view": {"max_count": 1},
    "ui": {},
    "watch": {"objects": [], "seconds": 0.01},
    "key": {"key": "", "mode": "press"},
    "input": {"action": ""},
}
# The engine-neutral ops the Unity and Godot plugins answer in place of Unreal's rc-based reads (docs/protocol.md,
# Engines): required of those plugins too
ENGINE_OPS = {
    "find": {"limit": 1},
    "transforms": {"objects": []},
    "pose": {},
    "movement": {},
}


def required_ops(hello):
    """The ops a plugin must have, by the engine its hello says (none said: Unreal)."""
    engine = str((hello or {}).get("engine") or "unreal").lower()
    return REQUIRED_OPS if engine == "unreal" else {**REQUIRED_OPS, **ENGINE_OPS}


class Response:
    """Enough of requests.Response for a BridgeError's response."""

    def __init__(self, status_code, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            return json.loads(self.text)
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}: {self.text}", response=self)


class BridgeError(requests.HTTPError):
    """The game answered a request with an error. An HTTPError with a response, like Remote Control's
    errors (e.g. "does not exist")."""

    def __init__(self, status, error):
        super().__init__(f"{status}: {error}", response=Response(status or 400, text=error))
        self.status, self.error = status, error


class Link:
    """One connection to the plugin. send(frame) delivers a dict to it; feed(frame) takes one from it."""

    def __init__(self, send):
        self._send = send
        self._send_lock = threading.Lock()
        self._ids = itertools.count(1)
        self._pending = {}  # id -> [threading.Event, response frame or None, time.monotonic() when sent, op]
        self._posted = set()  # ids of requests sent without waiting (see post)
        self.async_errors = []  # error answers to them
        self._lock = threading.Lock()
        self.closed = None  # why the connection ended, once it has
        self.hello = None  # the plugin's hello frame
        self.game_time = None  # the latest game time the plugin reported
        self.game_time_at = None  # ... and the wall time (time.time()) it came in
        self.last_heartbeat = None  # wall time of the latest heartbeat
        self.findings = []  # finding dicts from in-engine detectors, in arrival order
        self.logs = []  # log events (errors and Blueprint warnings), in arrival order
        self.messages = queue.Queue()  # other frames from the plugin: start, stop, feedback
        self.listeners = []  # callables taking each event dict
        self.frame_listeners = []  # callables taking each `frame` (the recording: autopilot/media.py)
        self.session_changes = 0  # how many times the plugin reported the game session starting or stopping
        self.loopback = None  # whether the plugin is on this machine (None: not known): Jev's calls go only there
        self.session = None  # the session id the plugin was welcomed with (a reconnecting plugin resumes it)
        self._held = None  # frames sent while the plugin is away to reload (see suspend), or None
        self.resumes = 0  # how many times the plugin came back on a new connection
        self.rebound = []  # callables run after each rebind (the run's activity feed is sent again: autopilot/cli.py)
        self.bound = None  # the connection the link sends through now (serve_local's token for it)
        # What the connection said about its user, for runs through Aura (autopilot/aura.py): its "Authorization:
        # Bearer" token and its "X-Autopilot-Mode" (None when it sent none)
        self.auth_token = None
        self.mode = None

    # -- sending ------------------------------------------------------------------------------------

    def send(self, frame):
        if self.closed:
            raise requests.ConnectionError(f"the game is disconnected ({self.closed})")
        with self._send_lock:
            if self._held is not None:  # (the plugin is reloading: sent once it is back)
                if len(self._held) >= 10000:
                    raise requests.ConnectionError("the game is reconnecting (too much waiting for it)")
                self._held.append(frame)
                return
            self._send(frame)

    def suspend(self, bound=None):
        """The plugin's connection closed for a reload (RELOAD_CLOSE): hold what is sent until it resumes. Requests
        keep waiting for their answers (the plugin answers them on the new connection). bound: the connection that
        closed (see rebind); nothing is held, and False returned, when the link has moved on to another already."""
        with self._send_lock:
            if bound is not None and self.bound is not bound:
                return False
            if self._held is None:
                self._held = []
            return True

    @property
    def suspended(self):
        return self._held is not None

    def give_up(self, bound, why):
        """The plugin that closed for a reload (on the connection `bound`) didn't come back: the link ends, unless it
        has resumed meanwhile. Returns whether it ended."""
        with self._send_lock:
            if self.bound is not bound or self._held is None:
                return False
            self.close(why)
            return True

    def rebind(self, send, bound=None):
        """The plugin came back on a new connection (hello {"resume": session}): send through it, what was held
        first. bound: a token for that connection (Link.bound), set in the same step, so the old connection's end,
        however late, can't suspend the link after it."""
        with self._send_lock:
            self._send = send
            self.bound = bound
            held, self._held = self._held or [], None
            self.resumes += 1
            for frame in held:
                send(frame)
        for fn in list(self.rebound):
            try:
                fn()
            except Exception as e:  # a listener's bug must not end the connection
                print(f"bridge rebind listener failed: {e}", file=sys.stderr)

    def status(self, text):
        """A progress line for the plugin's Autopilot tab. The same line twice running (the run's setup and then
        its session both say what stage it is) goes once."""
        if text == getattr(self, "_last_status", None):
            return
        self._last_status = text
        try:
            self.send({"type": "status", "text": text})
        except requests.ConnectionError:
            pass

    def request(self, op, args=None, timeout=None):
        """Send a request and wait for its answer. Raises BridgeError for an error answer, and
        requests.ConnectionError if the connection is gone or the game doesn't answer in time."""
        return self.request_at(op, args, timeout)[0]

    def request_at(self, op, args=None, timeout=None):
        """request(), with the game time the answer was worked out at (the game's frame then, as the answer tells it;
        None when it doesn't): (result, game time). A look at what the player sees is of that moment, however long
        the answer then took to come back."""
        args = args or {}
        rid = next(self._ids)
        done = threading.Event()
        entry = [done, None, time.monotonic(), op]
        with self._lock:
            self._pending[rid] = entry
        try:
            self.send({"type": "request", "id": rid, "op": op, "args": args})
            if timeout is None:
                timeout = TIMEOUT + (float(args.get("seconds", 0)) + DEFERRED_SLACK if op in DEFERRED_OPS else 0)
            if not done.wait(timeout):
                raise requests.ConnectionError(f"the game didn't answer {op} within {timeout:.0f}s")
        finally:
            with self._lock:
                self._pending.pop(rid, None)
        frame = entry[1]
        if frame is None:
            raise requests.ConnectionError(f"the game is disconnected ({self.closed})")
        if not frame.get("ok"):
            raise BridgeError(frame.get("status", 400), frame.get("error", "error"))
        t = frame.get("t")
        return frame.get("result"), t if isinstance(t, (int, float)) and not isinstance(t, bool) else None

    def post(self, op, args=None):
        """Send a request without waiting for its answer (an error answer is kept in async_errors)."""
        args = args or {}
        rid = next(self._ids)
        with self._lock:
            self._posted.add(rid)
        self.send({"type": "request", "id": rid, "op": op, "args": args})

    def missing_ops(self, probes=None):
        """The ops the brain needs that this plugin doesn't have (an older build, or an old copy of the plugin
        that the project loaded instead). Each is tried with arguments that change nothing (an empty key name, an
        unresolvable action, a read): any answer but "unknown op" means the op is there."""
        missing = []
        for op, args in (probes or required_ops(self.hello)).items():
            try:
                self.request(op, args, timeout=10)
            except BridgeError as e:
                if str(e.error).lower().startswith("unknown op"):
                    missing.append(op)
        return missing

    # -- receiving ----------------------------------------------------------------------------------

    def feed(self, frame):
        """Handle one frame from the plugin."""
        kind = frame.get("type")
        if frame.get("t") is not None:
            self.game_time, self.game_time_at = frame["t"], time.time()
        if kind == "response":
            with self._lock:
                entry = self._pending.get(frame.get("id"))
                posted = frame.get("id") in self._posted
                self._posted.discard(frame.get("id"))
            if posted and not frame.get("ok"):
                self.async_errors = (self.async_errors + [frame.get("error")])[-20:]
            if entry:
                entry[1] = frame
                entry[0].set()
        elif kind == "events":
            for event in frame.get("events", []):
                self._event(event)
        elif kind == "hello":
            self.hello = frame
            self.last_heartbeat = time.time()
            self.messages.put(frame)
        elif kind == "inspect":
            pass  # the Autopilot tab asks for Jev's calls: Autopilot sends them on its own (autopilot/runtime.py)
        elif kind == "frame":
            for listener in list(self.frame_listeners):  # (none: nobody records, the frame is dropped)
                try:
                    listener(frame)
                except Exception as e:
                    print(f"bridge frame listener failed: {e}", file=sys.stderr)
        else:
            self.messages.put(frame)

    def _event(self, event):
        kind = event.get("kind")
        if kind == "heartbeat":
            self.last_heartbeat = time.time()
        elif kind == "finding":
            finding = dict(event.get("finding", {}))
            finding.setdefault("t", event.get("t"))
            self.findings.append(finding)
        elif kind == "log":
            self.logs.append(event)
        elif kind == "session":
            self.session_changes += 1
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception as e:  # a listener's bug must not stop the link
                print(f"bridge listener failed: {e}", file=sys.stderr)

    def close(self, why):
        """The connection ended: fail every request still waiting."""
        if self.closed:
            return
        self.closed = why or "closed"
        with self._lock:
            pending = list(self._pending.values())
        for entry in pending:
            entry[0].set()
        self.messages.put({"type": "closed", "why": self.closed})

    def next_message(self, timeout=None):
        """The next non-request frame from the plugin (start, stop, feedback, closed), or None."""
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None


def is_loopback(address):
    """Whether a connection's remote address ((host, port, ...) as websockets gives it) is this machine's."""
    try:
        ip = ipaddress.ip_address(str(address[0]).split("%", 1)[0])
        return bool(ip.is_loopback or (getattr(ip, "ipv4_mapped", None) and ip.ipv4_mapped.is_loopback))
    except (TypeError, ValueError, IndexError):
        return False


def connection_auth(ws):
    """The (Authorization bearer token, X-Autopilot-Mode) a WebSocket connection was opened with, each None if
    absent."""
    headers = getattr(getattr(ws, "request", None), "headers", None)
    if headers is None:
        return None, None
    auth = str(headers.get("Authorization") or "").strip()
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    mode = str(headers.get("X-Autopilot-Mode") or "").strip().lower()
    return token or None, mode or None


BRAIN_FILE = "brain.json"


def brain_file(name=BRAIN_FILE):
    """Where a running `autopilot serve` says where it is, for the plugins to find it whatever its port (an engine
    plugin left at its default address reads it): Autopilot/<name> in the user's settings folder (%LOCALAPPDATA% on
    Windows, ~/Library/Application Support on macOS, $XDG_CONFIG_HOME or ~/.config elsewhere; the Unreal plugin's
    PlaytestAura::SettingsDir names the same, not Unreal's UserSettingsDir, which is under Epic/ on macOS). `name`:
    brain.json for an Autopilot of its own; a brain that serves another product says so under another name, which only
    plugins set to use that product read."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, SETTINGS_FOLDER, name)


SETTINGS_FOLDER = "Autopilot"


def announce(url, path=None, extra=None):
    """Write {"url", "pid", "started", "commit"} to brain_file() for as long as this process runs (the last brain
    started wins; one that ends removes the file only while it is still its own). commit: the build's
    (autopilot.build(), "" for a checkout): a plugin carrying another agent service tells this one is older. extra:
    more to say there ("control": where `autopilot mcp` reaches it, autopilot/agents/external.py)."""
    path = path or brain_file()
    pid = os.getpid()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{pid}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"url": url, "pid": pid, "started": time.time(), "commit": autopilot.build(), **(extra or {})}, f)
        os.replace(tmp, path)
    except OSError as e:
        print(f"note: couldn't write {path}: {e}", file=sys.stderr)
        return

    def withdraw():
        try:
            with open(path, encoding="utf-8") as f:
                ours = json.load(f).get("pid") == pid
            if ours:  # (closed first: Windows can't remove a file that is open)
                os.remove(path)
        except (OSError, ValueError, AttributeError):
            pass

    atexit.register(withdraw)


def serve_local(host="127.0.0.1", port=8765, wait=None, role="cli", announce_as=BRAIN_FILE, extra=None,
                roblox_port=None):
    """Listen for the plugin on this machine and return the Link once it has said hello (a service says where it is
    in brain_file(announce_as), with extra: announce()). A Roblox Studio plugin says hello over HTTP instead, on
    roblox_port (autopilot/roblox_bridge.py; None: AUTOPILOT_ROBLOX_PORT, else 8766; 0: none). Keeps
    listening afterwards: a plugin that reconnects (e.g. after the editor restarts) takes over the link's
    place in a fresh Link, available from the returned link's .server.current. A plugin that closed with RELOAD_CLOSE
    and says hello with {"resume": <its session>} within RESUME_SECONDS gets its own Link back: the same run goes on,
    and requests waiting across the reload are answered."""
    from websockets.sync.server import serve

    ready = queue.Queue()
    state = {"current": None, "sessions": {}}

    def handler(ws):
        try:
            first = json.loads(ws.recv(timeout=10))
        except Exception:
            return
        if first.get("type") != "hello" or first.get("protocol") != PROTOCOL:
            ws.send(json.dumps({"type": "error", "message": f"expected hello with protocol {PROTOCOL}"}))
            return
        send = lambda frame: ws.send(json.dumps(frame))  # noqa: E731
        token = object()  # (this connection's, Link.bound: a later one may take the link over)
        link = state["sessions"].get(first.get("resume")) if first.get("resume") else None
        resumed = link is not None and not link.closed
        if resumed:
            link.loopback = is_loopback(ws.remote_address)
            link.auth_token, link.mode = connection_auth(ws)
            link.hello = first
            link.last_heartbeat = time.time()
            try:
                ws.send(json.dumps({"type": "welcome", "role": role, "session": link.session,
                                    "features": BRAIN_FEATURES}))
                link.rebind(send, token)
            except Exception:
                pass  # (gone again already: its close, below, decides)
            print(f"The plugin is back after reloading its scripts (session {link.session})")
        else:
            link = Link(send)
            link.loopback = is_loopback(ws.remote_address)
            link.auth_token, link.mode = connection_auth(ws)
            link.session = f"local-{uuid.uuid4().hex[:12]}"
            link.bound = token
            link.feed(first)
            state["sessions"][link.session] = link
            ws.send(json.dumps({"type": "welcome", "role": role, "session": link.session, "features": BRAIN_FEATURES}))
            state["current"] = link
            ready.put(link)
        why, code = "the plugin disconnected", None
        try:
            for message in ws:
                try:
                    link.feed(json.loads(message))
                except ValueError:
                    continue
        except Exception as e:
            why = str(e) or "connection lost"
            code = getattr(getattr(e, "rcvd", None), "code", None)
        if code is None:
            code = getattr(ws, "close_code", None)
        if code == RELOAD_CLOSE:
            if not link.suspend(token):
                return  # (already resumed on a newer connection)

            def expire():
                if link.give_up(token, f"the plugin didn't come back within {RESUME_SECONDS:.0f}s of reloading"):
                    state["sessions"].pop(link.session, None)
            timer = threading.Timer(RESUME_SECONDS, expire)
            timer.daemon = True
            timer.start()
            return
        if link.bound is not token:
            return  # (already resumed on a newer connection)
        end(link, why)

    def end(link, why):
        link.close(why)
        state["sessions"].pop(link.session, None)  # (no longer resumable)

    server = serve(handler, host, port, max_size=None)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    roblox = _serve_roblox(host, roblox_port, state, ready)
    if role == "service" and announce_as:  # (a brain that stays: the plugins may find it by brain_file())
        more = {**(extra or {}), **({"roblox": roblox.url} if roblox else {})}
        announce(f"ws://{host}:{server.socket.getsockname()[1]}/v1/bridge", brain_file(announce_as), more)
    try:
        link = ready.get(timeout=wait)
    except queue.Empty:
        server.shutdown()
        if roblox:
            roblox.shutdown()
        raise TimeoutError(f"the plugin didn't connect to ws://{host}:{port} within {wait}s")
    link.server = server
    link.server_state = state
    link.roblox = roblox
    return link


def _serve_roblox(host, port, state, ready):
    """The Roblox Studio plugin's HTTP front door (autopilot/roblox_bridge.py) beside the WebSocket: its sessions are
    links like a WebSocket plugin's, handed out the same way. port: None for AUTOPILOT_ROBLOX_PORT (default 8766), 0
    for none. None when there is none (or its port is taken: the other engines go on)."""
    from . import roblox_bridge

    port = roblox_bridge.port() if port is None else port
    if not port:
        return None

    def on_link(link):
        state["sessions"][link.session] = link
        state["current"] = link
        ready.put(link)

    try:
        rb = roblox_bridge.RobloxBridge(on_link, host, port)
    except OSError as e:
        print(f"note: no Roblox bridge on http://{host}:{port} ({e})", file=sys.stderr)
        return None
    print(f"Waiting for Roblox Studio on {rb.url} ...")
    return rb
