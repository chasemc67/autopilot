"""A developer's probe for the Roblox Studio plugin: the real HTTP bridge (autopilot/roblox_bridge.py) without the
agent service, and a way to send the plugin one request at a time from a shell. Stop `autopilot serve` first (they
share port 8766).

    python tools/roblox_probe.py serve [--log /tmp/roblox-probe.jsonl]     # the bridge, and a command port (8799)
    python tools/roblox_probe.py call <op> ['{"json": "args"}'] [--timeout 30]
    python tools/roblox_probe.py frames [--since N]                        # the plugin's frames (events, ...) so far
    python tools/roblox_probe.py sessions

Every frame the plugin sends is appended to the log (one JSON object a line, with a receive time).
"""

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autopilot import bridge, roblox_bridge  # noqa: E402

COMMAND_PORT = 8799


def serve(args):
    links = []
    frames = []
    lock = threading.Lock()
    log = open(args.log, "a", encoding="utf-8")

    def keep(frame):
        with lock:
            frames.append({"at": round(time.time(), 3), **frame})
            log.write(json.dumps(frames[-1]) + "\n")
            log.flush()

    def on_link(link):
        feed = link.feed

        def logged_feed(frame):
            if frame.get("type") != "response":
                keep(frame)
            return feed(frame)

        link.feed = logged_feed
        links.append(link)
        keep({"type": "probe", "note": f"new session {getattr(link, 'session', '?')}", "hello": link.hello})

    rb = roblox_bridge.RobloxBridge(on_link, screenshots=args.screenshots)
    print(f"bridge on {rb.url}; commands on http://127.0.0.1:{COMMAND_PORT}", flush=True)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/frames"):
                since = int(self.path.split("since=")[1]) if "since=" in self.path else 0
                with lock:
                    self._reply(200, {"count": len(frames), "frames": frames[since:]})
            elif self.path.startswith("/sessions"):
                out = []
                for sid, s in rb.sessions.items():
                    out.append({"id": sid, "closed": s.closed,
                                "channels": {c: {"gone": ch.gone, "age": round(time.monotonic() - ch.last_seen, 1)}
                                             for c, ch in s.channels.items()}})
                self._reply(200, {"latest": getattr(rb.latest, "id", None), "sessions": out})
            else:
                self._reply(404, {})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            live = [lk for lk in links if not lk.closed]
            if not live:
                self._reply(409, {"error": "no plugin session"})
                return
            started = time.time()
            try:
                result = live[-1].request(body["op"], body.get("args") or {}, timeout=body.get("timeout") or 30)
                self._reply(200, {"ok": True, "result": result, "seconds": round(time.time() - started, 3)})
            except bridge.BridgeError as e:
                self._reply(200, {"ok": False, "status": e.status, "error": e.error,
                                  "seconds": round(time.time() - started, 3)})
            except Exception as e:  # noqa: BLE001
                self._reply(200, {"ok": False, "error": f"{type(e).__name__}: {e}",
                                  "seconds": round(time.time() - started, 3)})

    ThreadingHTTPServer(("127.0.0.1", COMMAND_PORT), Handler).serve_forever()


def _http(method, path, body=None, timeout=600):
    req = urllib.request.Request(f"http://127.0.0.1:{COMMAND_PORT}{path}", method=method,
                                 data=None if body is None else json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--log", default="/tmp/roblox-probe.jsonl")
    s.add_argument("--screenshots", action="store_true", default=None)
    c = sub.add_parser("call")
    c.add_argument("op")
    c.add_argument("args", nargs="?", default="{}")
    c.add_argument("--timeout", type=float, default=30)
    c.add_argument("--max", type=int, default=4000, help="truncate the printed answer to this many characters")
    f = sub.add_parser("frames")
    f.add_argument("--since", type=int, default=0)
    sub.add_parser("sessions")
    a = ap.parse_args(argv)
    if a.cmd == "serve":
        serve(a)
    elif a.cmd == "call":
        out = json.dumps(_http("POST", "/", {"op": a.op, "args": json.loads(a.args), "timeout": a.timeout}), indent=1)
        print(out if len(out) <= a.max else out[: a.max] + f"\n... ({len(out)} characters)")
    elif a.cmd == "frames":
        print(json.dumps(_http("GET", f"/frames?since={a.since}"), indent=1))
    else:
        print(json.dumps(_http("GET", "/sessions"), indent=1))


if __name__ == "__main__":
    main()
