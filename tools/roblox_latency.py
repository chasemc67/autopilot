"""Round-trip times of the Roblox plugin's answers, through tools/roblox_probe.py's command port (a play test must
be running): sequential server ops, client ops, and a burst of concurrent ones (as game.batch sends them).

    python tools/roblox_latency.py [-n 20]
"""

import argparse
import concurrent.futures as futures
import json
import statistics
import time
import urllib.request


def call(op, args=None):
    req = urllib.request.Request("http://127.0.0.1:8799/", method="POST",
                                 data=json.dumps({"op": op, "args": args or {}, "timeout": 30}).encode())
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=60) as r:
        answer = json.loads(r.read())
    if not answer.get("ok"):
        raise RuntimeError(answer)
    return time.monotonic() - started


def stats(name, times):
    ms = sorted(t * 1000 for t in times)
    print(f"{name:28s} n={len(ms):3d} median={statistics.median(ms):6.0f} ms  p90={ms[int(len(ms) * 0.9) - 1]:6.0f} ms"
          f"  max={ms[-1]:6.0f} ms")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args()
    stats("server op (context)", [call("context") for _ in range(a.n)])
    stats("client op (pose)", [call("pose") for _ in range(a.n)])
    bursts = []
    with futures.ThreadPoolExecutor(8) as pool:
        for _ in range(max(1, a.n // 4)):
            started = time.monotonic()
            list(pool.map(lambda op: call(op), ["pose", "context", "pose", "context", "pose", "context"]))
            bursts.append(time.monotonic() - started)
    stats("burst of 6 concurrent", bursts)


if __name__ == "__main__":
    main()
