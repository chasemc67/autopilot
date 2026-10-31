"""Where model-written code runs: a separate process without Autopilot's secrets.

Harness and probe code is written by a language model that reads the game's code, and a game's code could carry
instructions to that model (prompt injection). So that code runs in a child process that has no API keys and, on
Linux with bubblewrap, no network, no view of other processes and a read-only view of the disk but for this project's
memory folder.
Everything it does to the game and every Jev question goes through the trusted parent, which holds the real link to
the plugin and the keyed Jev client:

    parent (keys, link)  <== JSON lines over stdin/stdout ==>  child (python -m autopilot.sandbox)
      link.request/post/send/status <---- "req" / "post" / "send" / "status" ----  ProxyLink (Game's link)
      Jev client.system_one        <---- "jev" ---------------------------------  RemoteJev (the runtime's client)
      plugin events (findings)     ----- "event" ------------------------------>  ProxyLink.listeners
      Session.<method>(...)        ----- "call" ------------------------------->  a Session of its own
      media.Recorder.begin/end     <---- "episode" -----------------------------  RemoteRecorder (each episode's video)
      Session.activity(...)        <---- "activity" ----------------------------  Session.forward (the feed's events)

AUTOPILOT_SANDBOX: "auto" (default: bubblewrap where available, else a plain child process), "bwrap" (required),
"process", or "off" (in this process: tests and debugging only). On Windows and macOS "auto" is a plain child
process: it keeps the keys from model-written code, but not the network or the disk.
"""

import concurrent.futures as futures
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
import types

from . import bridge
from .words import plain_words

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mode():
    return (os.environ.get("AUTOPILOT_SANDBOX") or "auto").strip().lower()


def _secret_pattern():
    from .agents.session import SECRET

    return SECRET


def child_env(memory):
    """The child's environment: nothing secret, only what Python and autopilot need."""
    secret = _secret_pattern()
    env = {k: v for k, v in os.environ.items() if not secret.search(k)}
    env.update(PYTHONPATH=HERE, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", AUTOPILOT_MEMORY=memory,
               AUTOPILOT_SANDBOX="child")
    from .runtime import JEV_VIA, jev_via

    env[JEV_VIA] = jev_via()  # (how Jev is reached, for the child's trace: it holds no key to tell by)
    return env


def bwrap_command(root, memory):
    """bubblewrap around the child: a read-only view of the system, no network, its own process tree, this project's
    memory folder writable, the agent's session files hidden."""
    bwrap = shutil.which("bwrap")
    if not bwrap:
        return None
    cmd = [bwrap, "--unshare-all", "--die-with-parent", "--new-session",
           "--ro-bind", "/", "/", "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
    cmd += ["--bind", root, root]  # this project's memory, writable
    agent_dir = os.path.join(root, ".agent")
    if os.path.isdir(agent_dir):
        cmd += ["--tmpfs", agent_dir]  # (Claude Code's session files: the implementer's prompts)
    cmd += ["--chdir", root, "--clearenv"]
    for k, v in child_env(memory).items():
        if k in ("PATH", "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONIOENCODING", "AUTOPILOT_MEMORY", "AUTOPILOT_SANDBOX",
                 "LANG"):
            cmd += ["--setenv", k, v]
    cmd += ["--setenv", "HOME", "/tmp", "--"]
    return cmd


class SandboxError(RuntimeError):
    pass


class Sandbox:
    """The parent side: starts the child and serves its requests with the real link and Jev client."""

    def __init__(self, link, client, project, prompt, run_id, root, memory, scrub=None, recorder=None, level=None,
                 say=None, activity=None):
        self.link, self.client = link, client
        self.recorder = recorder  # (the run's media.Recorder: the episodes the child plays mark their videos on it)
        self.scrub = scrub or (lambda x: x)
        # The child's progress lines and activity events (docs/protocol.md): the parent session's say() and
        # activity(), so its stage and its feed know what the child does; without them, the link's status alone
        self.say = say or (lambda text: self.link.status(text))
        self.activity = activity
        self.lock = threading.Lock()
        self.calls = {}
        self.ids = itertools.count(1)
        self.pool = futures.ThreadPoolExecutor(max_workers=24, thread_name_prefix="sandbox")
        m = mode()
        base = [sys.executable, "-u", "-m", "autopilot.sandbox"]
        wrapped = bwrap_command(root, memory) if m in ("auto", "bwrap") and os.name != "nt" else None
        if m == "bwrap" and not wrapped:
            raise SandboxError("AUTOPILOT_SANDBOX=bwrap but bubblewrap isn't available")
        self.kind = "bwrap" if wrapped else "process"
        self.proc = subprocess.Popen((wrapped or []) + base, cwd=root, env=child_env(memory), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=open(os.path.join(root, "sandbox.log"), "ab"),
                                     bufsize=0)
        self._write({"type": "init", "project": project, "prompt": prompt, "run_id": run_id,
                     "hello": getattr(link, "hello", None) or {}, "loopback": bool(getattr(link, "loopback", False)),
                     "level": level})
        self.listener = lambda event: self._write({"type": "event", "event": event})
        if isinstance(getattr(link, "listeners", None), list):
            link.listeners.append(self.listener)
        threading.Thread(target=self._read, daemon=True).start()
        ready = self.call("ping", timeout=60)
        if ready != "pong":
            raise SandboxError(f"the sandbox didn't start ({self.kind}): {ready}")

    def _write(self, msg):
        data = (json.dumps(msg, default=str) + "\n").encode("utf-8")
        with self.lock:
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass

    def _read(self):
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "reply":
                entry = self.calls.get(msg.get("id"))
                if entry:
                    entry[1] = msg
                    entry[0].set()
            elif kind == "req":
                self.pool.submit(self._request, msg)
            elif kind == "post":
                try:
                    self.link.post(msg["op"], msg.get("args") or {})
                except Exception:
                    pass
            elif kind == "jev":
                self.pool.submit(self._jev, msg)
            elif kind == "episode":
                self.pool.submit(self._episode, msg)
            elif kind == "send":
                try:
                    self.link.send(self.scrub(msg["frame"]))
                except Exception:
                    pass
            elif kind == "status":
                try:
                    # (the run's progress, in the user's words: it reaches the plugin as it is)
                    self.say(plain_words(self.scrub(str(msg.get("text", "")))))
                except Exception:
                    pass
            elif kind == "activity":
                if self.activity is not None:
                    try:  # (the parent's session numbers, logs, scrubs and sends it)
                        self.activity(**msg["event"])
                    except Exception:
                        pass
        for entry in list(self.calls.values()):
            entry[0].set()

    def _request(self, msg):
        from .bridge import BridgeError

        try:
            result, t = self.link.request_at(msg["op"], msg.get("args") or {}, timeout=msg.get("timeout"))
            reply = {"type": "res", "rid": msg["rid"], "ok": True, "result": result, "t": t}
        except BridgeError as e:
            reply = {"type": "res", "rid": msg["rid"], "ok": False, "status": e.status, "error": str(e.error)}
        except Exception as e:
            reply = {"type": "res", "rid": msg["rid"], "ok": False, "conn": f"{type(e).__name__}: {e}"}
        reply["game_time"] = getattr(self.link, "game_time", None)
        self._write(reply)

    def _episode(self, msg):
        """An episode the child plays begins or ends: its video (media.Recorder.begin/end; end takes its end picture
        before it answers, so the child pauses the game after)."""
        try:
            if self.recorder is not None and msg.get("mark") in ("begin", "end"):
                getattr(self.recorder, msg["mark"])(int(msg["n"]))
            reply = {"type": "res", "rid": msg["rid"], "ok": True}
        except Exception as e:
            reply = {"type": "res", "rid": msg["rid"], "ok": False, "error": f"{type(e).__name__}: {e}"}
        self._write(reply)

    def _jev(self, msg):
        from typesafe_sdk import Choice, Noul, Score

        from .runtime import answer_as_sent as _answer_as_sent

        kinds = {"choice": Choice, "noul": Noul, "score": Score}
        try:
            questions = {k: kinds[d.get("type", "choice")].model_validate(d) for k, d in msg["questions"].items()}
            r = self.client.system_one(state=msg["state"], questions=questions)
            out = {"choices": {k: {"choice": a.choice, "probabilities": dict(getattr(a, "probabilities", None) or {}),
                                   "confidence": getattr(a, "confidence", None)} for k, a in r.choices.items()},
                   "nouls": {k: a.noul for k, a in r.nouls.items()},
                   "scores": {k: {"score": a.score, "probabilities": getattr(a, "probabilities", None)}
                              for k, a in r.scores.items()},
                   "raw": _answer_as_sent(r)}
            reply = {"type": "res", "rid": msg["rid"], "ok": True, "result": out}
        except Exception as e:
            reply = {"type": "res", "rid": msg["rid"], "ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
        self._write(reply)

    def call(self, method, timeout=None, **kwargs):
        """Run Session.<method>(**kwargs) in the child; returns its reply's value."""
        cid = next(self.ids)
        entry = [threading.Event(), None]
        self.calls[cid] = entry
        self._write({"type": "call", "id": cid, "method": method, "kwargs": kwargs})
        try:
            if not entry[0].wait(timeout):
                raise SandboxError(f"the sandbox didn't answer {method} in time")
        finally:
            self.calls.pop(cid, None)
        reply = entry[1]
        if reply is None:
            raise SandboxError(f"the sandbox ended ({self.proc.poll()})")
        if reply.get("error"):
            raise SandboxError(reply["error"])
        return reply.get("value")

    def stop(self):
        self._write({"type": "stop"})

    def end_episode(self):
        """Ends the episode running in the child now (the user steered), not the run."""
        self._write({"type": "end_episode"})

    def close(self):
        if isinstance(getattr(self.link, "listeners", None), list) and self.listener in self.link.listeners:
            self.link.listeners.remove(self.listener)
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.pool.shutdown(wait=False, cancel_futures=True)


# -- the child ---------------------------------------------------------------------------------------------------

class _Out:
    """The child's line writer to the parent (stdout is the protocol; anything printed goes to stderr)."""

    def __init__(self):
        self.f = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", newline="\n")
        self.lock = threading.Lock()
        sys.stdout = sys.stderr

    def __call__(self, msg):
        with self.lock:
            self.f.write(json.dumps(msg, default=str) + "\n")
            self.f.flush()


class ProxyLink:
    """bridge.Link's surface for Game and the runtime, served by the parent."""

    def __init__(self, out, hello, loopback):
        self.out = out
        self.hello = hello
        self.loopback = loopback
        self.listeners = []
        self.backlog = bridge.new_backlog()  # (bridge.keep_unheard)
        self.game_time = None
        self.closed = None
        self.pending = {}
        self.ids = itertools.count(1)

    def _ask(self, msg, timeout=None):
        rid = next(self.ids)
        entry = [threading.Event(), None]
        self.pending[rid] = entry
        msg["rid"] = rid
        self.out(msg)
        try:
            if not entry[0].wait(timeout or 120):
                import requests

                raise requests.ConnectionError(f"no answer to {msg.get('op') or msg.get('type')} in time")
        finally:
            self.pending.pop(rid, None)
        return entry[1]

    def answer(self, msg):
        if msg.get("game_time") is not None:
            self.game_time = msg["game_time"]
        entry = self.pending.get(msg.get("rid"))
        if entry:
            entry[1] = msg
            entry[0].set()

    def request_at(self, op, args=None, timeout=None):
        import requests

        from .bridge import BridgeError

        r = self._ask({"type": "req", "op": op, "args": args or {}, "timeout": timeout},
                      timeout=(timeout or 30) + 15)
        if r.get("ok"):
            if r.get("t") is not None:
                self.game_time = r["t"]
            return r.get("result"), r.get("t")
        if r.get("conn"):
            raise requests.ConnectionError(r["conn"])
        raise BridgeError(r.get("status") or 400, r.get("error") or "error")

    def request(self, op, args=None, timeout=None):
        return self.request_at(op, args, timeout)[0]

    def post(self, op, args=None):
        self.out({"type": "post", "op": op, "args": args or {}})

    def send(self, frame):
        self.out({"type": "send", "frame": frame})

    def status(self, text):
        self.out({"type": "status", "text": text})


class RemoteJev:
    """A Jev client whose questions the parent asks (the child has no key)."""

    def __init__(self, link):
        self.link = link

    def system_one(self, state, questions):
        dumps = {k: q.model_dump(mode="json", exclude_none=True) for k, q in questions.items()}
        r = self.link._ask({"type": "jev", "state": state, "questions": dumps}, timeout=10)
        if not r.get("ok"):
            raise RuntimeError(r.get("error") or "Jev failed")
        res = r["result"]
        ns = types.SimpleNamespace
        return ns(choices={k: ns(**v) for k, v in res["choices"].items()},
                  nouls={k: ns(noul=v) for k, v in res["nouls"].items()},
                  scores={k: ns(**v) for k, v in res["scores"].items()}, usage=None,
                  raw=res.get("raw"))  # (the answer as the decision model's API gave it, for the run's data)


class RemoteRecorder:
    """The parent's media.Recorder, for the episodes played here: begin(n) and end(n) mark episode n's video (end
    waits for its end picture). Never raises: a video lost is no reason to lose the episode."""

    def __init__(self, link):
        self.link = link

    def _mark(self, mark, n, timeout):
        try:
            self.link._ask({"type": "episode", "mark": mark, "n": n}, timeout=timeout)
        except Exception:
            pass

    def begin(self, n):
        self._mark("begin", n, 10)

    def end(self, n):
        self._mark("end", n, 20)


def child_main():
    out = _Out()
    reader = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    init = json.loads(reader.readline())
    link = ProxyLink(out, init.get("hello") or {}, init.get("loopback"))
    stopped = threading.Event()

    from . import codex as codexmod
    from . import runtime
    from .agents.session import Session
    from .kit.game import Game

    game = Game(link, codexmod.Codex.for_project(init["project"]))
    game.level = init.get("level")  # (the run's level: restarts start it over)
    session = Session(game, init["project"], init["prompt"], init["run_id"], say=link.status, client=RemoteJev(link))
    session.forward = lambda event: out({"type": "activity", "event": event})  # (the parent's feed has it)
    session.stop = stopped.is_set
    session.recorder = RemoteRecorder(link)
    pool = futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="call")

    def run_call(msg):
        reply = {"type": "reply", "id": msg["id"]}
        try:
            if msg["method"] == "ping":
                reply["value"] = "pong"
            elif msg["method"] == "state":
                reply["value"] = {"episodes": session.episodes, "played": session.played,
                                  "most_decisions": session.most_decisions, "writes": runtime.jsonable(game.writes)}
            elif msg["method"] in ("pause", "release_all"):
                getattr(game, msg["method"])(**msg.get("kwargs", {}))
                reply["value"] = True
            elif msg["method"] in ("probe", "run_harness", "snapshot", "restart_level", "opening"):
                reply["value"] = getattr(session, msg["method"])(**msg.get("kwargs", {}))
            else:
                reply["error"] = f"unknown call {msg['method']}"
        except Exception:
            reply["error"] = traceback.format_exc(limit=5)[-1500:]
        out(reply)

    for line in reader:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        kind = msg.get("type")
        if kind == "res":
            link.answer(msg)
        elif kind == "event":
            bridge.keep_unheard(link, msg["event"])
            for listener in list(link.listeners):
                try:
                    listener(msg["event"])
                except Exception:
                    pass
        elif kind == "call":
            pool.submit(run_call, msg)
        elif kind == "stop":
            stopped.set()
        elif kind == "end_episode":
            session.end_episode.set()
    link.closed = "the parent closed"


SELFTEST = r"""
import os, socket
print("secret env vars:", sorted(k for k in os.environ if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET"))))
try:
    socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    print("network: OPEN")
except OSError as e:
    print("network: blocked", type(e).__name__)
try:
    print("processes seen:", len([p for p in os.listdir("/proc") if p.isdigit()]))
except OSError:
    print("processes seen: (no /proc here)")
leaks = []
try:
    for p in [p for p in os.listdir("/proc") if p.isdigit()]:
        try:
            env = open(f"/proc/{p}/environ", "rb").read()
        except OSError:
            continue
        if any(w in env for w in (b"API_KEY", b"TOKEN", b"SECRET")):
            leaks.append(p)
except OSError:
    pass
print("processes whose environment holds a key:", leaks or "none")
others = [d for d in os.listdir(os.path.dirname(os.getcwd())) if d != os.path.basename(os.getcwd())]
print("other projects' memory seen:", others or "none")
print("this project writable:", os.access(os.getcwd(), os.W_OK))
"""


def selftest(root=None):
    """Start a sandbox as a run would and report what model-written code inside it can reach
    (python -m autopilot.sandbox --selftest)."""
    import tempfile

    root = root or tempfile.mkdtemp(prefix="autopilot-selftest-")
    os.makedirs(os.path.join(root, "runs", "selftest"), exist_ok=True)

    class StubLink:
        hello, loopback, listeners, game_time = {}, False, [], 0.0

        def request_at(self, op, args=None, timeout=None):
            return {}, 0.0

        def post(self, op, args=None):
            pass

        def send(self, frame):
            pass

        def status(self, text):
            pass

    sb = Sandbox(StubLink(), None, "selftest", "selftest", "selftest", root, os.path.dirname(root))
    try:
        print(f"sandbox: {sb.kind}")
        print(sb.call("probe", code=SELFTEST, timeout=60))
    finally:
        sb.close()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        child_main()
