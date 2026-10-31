"""What a run used and did: the tokens of every model call (for the plugin's Settings page), and, when the run's data is
shared, every call in full (docs/run-data.md).

    Usage       tokens per model, as the plugin's `usage` message carries them (docs/protocol.md, Usage).
    CountingJev the decision model's client, counting each call's tokens.
    LLMProxy    a proxy on this machine between the agent and its language model's API: each call goes through
                unchanged (to the provider, or to Aura's relay for a run through Aura) and is counted, and, when
                shared, written whole to telemetry/llm.jsonl (the request as sent, but for its pictures' data, and the
                response as the model gave it).
    RunTelemetry one run's: the above, the plugin told the usage as it grows, the recording (autopilot/media.py: every
                run's, for the plugin's report; kept with the data when shared), and at the end telemetry/run.json,
                the run's manifest.

The decision model's calls are written by the runtime itself (autopilot/runtime.py, telemetry/decisions<n>.jsonl),
beside the episode's trace: it is where each call's observation and the action taken are known.
"""

import gzip
import http.server
import json
import os
import platform
import shutil
import socketserver
import sys
import threading
import time
import uuid

import requests

SCHEMA = 1
TELEMETRY = "telemetry"  # the folder in a run's folder
USAGE_EVERY = 2.0  # seconds between two usage messages, at most
# A provider's API root when its <PROVIDER>_BASE_URL doesn't name one (what the agents use then)
UPSTREAMS = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1",
             "openrouter": "https://openrouter.ai/api/v1", "google": "https://generativelanguage.googleapis.com/v1beta"}
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length", "accept-encoding", "content-encoding"}
UPSTREAM_TIMEOUT = (15, 900)  # (connect, read between bytes) seconds


def sharing(hello):
    """Whether a run's data is shared: the user didn't say no in the plugin (hello `share`) or in the environment
    (AUTOPILOT_SHARE=0). A run through Aura also needs the user's Aura account to allow it (autopilot/training.py)."""
    if (os.environ.get("AUTOPILOT_SHARE") or "").strip() == "0":
        return False
    return (hello or {}).get("share", True) is not False


# -- tokens ---------------------------------------------------------------------------------------------------------

class Usage:
    """Tokens per (kind, model): kind "llm" (the agent's language model) or "decision" (the model playing)."""

    FIELDS = ("calls", "input", "output", "cache_read", "cache_write")

    def __init__(self):
        self.lock = threading.Lock()
        self.models = {}
        self.costs = {}  # (kind, model) -> US dollars, as the API said (Vercel AI Gateway says each call's)
        self.version = 0

    def add(self, kind, model, input=0, output=0, cache_read=0, cache_write=0, calls=1, cost_usd=None):
        key = (kind, str(model or "unknown"))
        with self.lock:
            m = self.models.setdefault(key, dict.fromkeys(self.FIELDS, 0))
            for field, n in zip(self.FIELDS, (calls, input, output, cache_read, cache_write)):
                m[field] += max(0, int(n or 0))
            if cost_usd:
                self.costs[key] = self.costs.get(key, 0.0) + max(0.0, float(cost_usd))
            self.version += 1

    def snapshot(self):
        """The models as the `usage` message lists them: the language model's first, then the decision model's (with
        "cost_usd" where the API said what the calls cost)."""
        with self.lock:
            items = sorted(self.models.items(), key=lambda kv: (kv[0][0] != "llm", kv[0][1]))
            return [{"kind": kind, "model": model, **m,
                     **({"cost_usd": round(self.costs[kind, model], 9)} if (kind, model) in self.costs else {})}
                    for (kind, model), m in items]

    def totals(self):
        """{kind: {field: n}} summed over models."""
        out = {}
        for m in self.snapshot():
            t = out.setdefault(m["kind"], dict.fromkeys(self.FIELDS, 0))
            for field in self.FIELDS:
                t[field] += m[field]
        return out


class CountingJev:
    """A decision-model client (typesafe_sdk's) whose calls' tokens go into a Usage. Everything else passes through."""

    def __init__(self, client, usage):
        self._client = client
        self._usage = usage

    def system_one(self, *args, **kwargs):
        r = self._client.system_one(*args, **kwargs)
        u = getattr(r, "usage", None)
        self._usage.add("decision", getattr(r, "model", None), input=getattr(u, "input_tokens", 0) or 0,
                        output=getattr(u, "output_tokens", 0) or 0, cost_usd=decision_cost(r))
        return r

    def __getattr__(self, name):
        return getattr(self._client, name)


def decision_cost(r):
    """What a decision model's call cost in US dollars, where its API said (Vercel AI Gateway's
    provider_metadata.gateway.cost in the response body), else None."""
    raw = getattr(r, "raw_http_response", None)
    try:
        body = json.loads(raw.content) if raw is not None else getattr(r, "raw", None)
        cost = ((body or {}).get("provider_metadata") or {}).get("gateway", {}).get("cost")
        return float(cost) if cost is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


def usage_line(models):
    """A run's usage (Usage.snapshot()) in one line: per model its calls, tokens and, where the API said, cost."""
    parts = []
    for m in models:
        cost = f", ${m['cost_usd']:.6f}" if "cost_usd" in m else ""
        cache = f", cache {m['cache_read']} read / {m['cache_write']} written" if m["cache_read"] or m["cache_write"] \
            else ""
        parts.append(f"{m['kind']} {m['model']}: {m['calls']} calls, {m['input']} in / {m['output']} out tokens"
                     f"{cache}{cost}")
    return "Usage: " + ("; ".join(parts) if parts else "none counted")


def llm_usage(body):
    """(model, {input, output, cache_read, cache_write}) from a language model API's response (Anthropic's Messages,
    OpenAI's chat completions or responses); usage None when it says none."""
    if not isinstance(body, dict):
        return None, None
    u = body.get("usage")
    if not isinstance(u, dict):
        return body.get("model"), None
    # (Anthropic's shape; Vercel AI Gateway's chat completions carry cache_creation_input_tokens beside prompt_tokens)
    if "prompt_tokens" not in u and ("cache_read_input_tokens" in u or "cache_creation_input_tokens" in u) or \
            body.get("type") == "message":
        return body.get("model"), {"input": u.get("input_tokens") or 0, "output": u.get("output_tokens") or 0,
                                   "cache_read": u.get("cache_read_input_tokens") or 0,
                                   "cache_write": u.get("cache_creation_input_tokens") or 0}
    prompt = u.get("prompt_tokens", u.get("input_tokens")) or 0
    details = u.get("prompt_tokens_details") or u.get("input_tokens_details") or {}
    cached = (details.get("cached_tokens") or 0) if isinstance(details, dict) else 0
    out = {"input": max(0, prompt - cached), "output": u.get("completion_tokens", u.get("output_tokens")) or 0,
           "cache_read": cached, "cache_write": 0}
    if isinstance(u.get("cost"), (int, float)):  # (Vercel AI Gateway's chat completions say what the call cost)
        out["cost_usd"] = u["cost"]
    return body.get("model"), out


# -- the language model's calls -------------------------------------------------------------------------------------

def without_pictures(value):
    """A request with each picture's data (base64: Anthropic's image source, a data: URL) replaced by its size, as
    llm.jsonl keeps it: a run's pictures are in its recording (describe_frames sends a dozen per call)."""
    if isinstance(value, list):
        return [without_pictures(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for k, v in value.items():
        if k == "data" and value.get("type") == "base64" and isinstance(v, str):
            out[k] = f"<{value.get('media_type') or 'picture'}: {len(v) * 3 // 4} bytes>"
        elif k == "url" and isinstance(v, str) and v.startswith("data:"):
            out[k] = f"<{v[5:v.find(';')] or 'picture'}: {(len(v) - v.find(',') - 1) * 3 // 4} bytes>"
        else:
            out[k] = without_pictures(v)
    return out


class SSE:
    """A server-sent event stream read as it passes: its events, and Anthropic's message put back together."""

    def __init__(self):
        self.buffer = b""
        self.events = []
        self.message = None  # Anthropic's Messages response, rebuilt from its events
        self.blocks = {}
        self.json_parts = {}
        self.other_usage = None  # the last `usage` another API's events carried
        self.model = None

    def feed(self, chunk):
        self.buffer += chunk.replace(b"\r\n", b"\n")
        while b"\n\n" in self.buffer:
            raw, self.buffer = self.buffer.split(b"\n\n", 1)
            data = b"\n".join(line[5:].lstrip() for line in raw.split(b"\n") if line.startswith(b"data:"))
            if not data or data == b"[DONE]":
                continue
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if isinstance(event, dict):
                self.events.append(event)
                self._anthropic(event)

    def _anthropic(self, e):
        kind = e.get("type")
        if kind == "message_start":
            self.message = dict(e.get("message") or {})
            self.message["content"] = []
        elif self.message is None:
            # (another API: its chunks may carry the model and, in the last one, the usage)
            response = e.get("response") if isinstance(e.get("response"), dict) else e
            if isinstance(response.get("usage"), dict):
                self.other_usage = response
            self.model = response.get("model") or self.model
        elif kind == "content_block_start":
            self.blocks[e.get("index", 0)] = dict(e.get("content_block") or {})
        elif kind == "content_block_delta":
            block = self.blocks.setdefault(e.get("index", 0), {})
            d = e.get("delta") or {}
            t = d.get("type")
            if t == "text_delta":
                block["text"] = block.get("text", "") + d.get("text", "")
            elif t == "thinking_delta":
                block["thinking"] = block.get("thinking", "") + d.get("thinking", "")
            elif t == "signature_delta":
                block["signature"] = block.get("signature", "") + d.get("signature", "")
            elif t == "input_json_delta":
                self.json_parts[e.get("index", 0)] = self.json_parts.get(e.get("index", 0), "") + d.get(
                    "partial_json", "")
            elif t == "citations_delta":
                block.setdefault("citations", []).append(d.get("citation"))
        elif kind == "content_block_stop":
            i = e.get("index", 0)
            block = self.blocks.get(i, {})
            if i in self.json_parts:
                try:
                    block["input"] = json.loads(self.json_parts[i] or "{}")
                except ValueError:
                    block["input"] = {"_unparsed": self.json_parts[i]}
        elif kind == "message_delta":
            self.message.update({k: v for k, v in (e.get("delta") or {}).items()})
            usage = dict(self.message.get("usage") or {})
            usage.update(e.get("usage") or {})
            self.message["usage"] = usage

    def result(self):
        """The response as one object: Anthropic's message (content in block order), else the other API's last
        response or chunk that had a usage; None when the stream had neither."""
        if self.message is not None:
            message = dict(self.message)
            message["content"] = [self.blocks[i] for i in sorted(self.blocks)]
            return message
        return self.other_usage


class LLMProxy:
    """A proxy on 127.0.0.1 in front of language model APIs: http://127.0.0.1:<port>/<provider>/... goes to that
    provider's API root as given (Aura's relay, a gateway, or the provider). Counts each call's tokens in `usage`, and
    with `path`, writes each call to it whole (JSON lines). Headers (the keys) are never written."""

    def __init__(self, usage, path=None):
        self.usage = usage
        self.path = path
        self.upstreams = {}
        self.write_lock = threading.Lock()
        self.calls = 0
        self.file = open(path, "a", encoding="utf-8") if path else None
        proxy = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                proxy.handle(self)

            do_GET = do_PUT = do_DELETE = do_PATCH = do_POST

        class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
            daemon_threads = True
            allow_reuse_address = False

            def handle_error(self, request, client_address):
                if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):  # (an agent hanging up: fine)
                    super().handle_error(request, client_address)

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.http = requests.Session()
        self.thread = threading.Thread(target=self.server.serve_forever, name="llm-proxy", daemon=True)
        self.thread.start()

    def url(self, provider):
        return f"http://127.0.0.1:{self.port}/{provider}"

    def route(self, provider, upstream):
        """Send /<provider>/... to upstream (an API root, e.g. https://api.anthropic.com)."""
        self.upstreams[provider] = upstream.rstrip("/")

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        with self.write_lock:
            if self.file:
                self.file.close()
                self.file = None

    # -- one call ---------------------------------------------------------------------------------------------

    @staticmethod
    def _body(handler):
        if "chunked" in (handler.headers.get("Transfer-Encoding") or "").lower():
            parts = []
            while True:
                size = int(handler.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    handler.rfile.readline()
                    return b"".join(parts)
                parts.append(handler.rfile.read(size))
                handler.rfile.readline()
        n = int(handler.headers.get("Content-Length") or 0)
        return handler.rfile.read(n) if n else b""

    def handle(self, h):
        started, first_byte = time.time(), None
        provider, _, rest = h.path.lstrip("/").partition("/")
        upstream = self.upstreams.get(provider)
        body = self._body(h)
        record = {"schema": SCHEMA, "provider": provider, "method": h.command, "path": "/" + rest.split("?")[0],
                  "started": round(started, 3)}
        try:
            request = json.loads(body) if body else None
        except ValueError:
            request = None
        record["request"] = without_pictures(request)
        if not upstream:
            self._reply_error(h, 502, f"no upstream for {provider!r}")
            return
        headers = {k: v for k, v in h.headers.items() if k.lower() not in HOP}
        headers["Accept-Encoding"] = "identity"
        sse, raw = SSE(), []
        status, answered = 502, False
        try:
            r = self.http.request(h.command, f"{upstream}/{rest}", headers=headers, data=body or None, stream=True,
                                  timeout=UPSTREAM_TIMEOUT)
            status = r.status_code
            streaming = "text/event-stream" in (r.headers.get("Content-Type") or "")
            h.send_response(r.status_code)
            for k, v in r.headers.items():
                if k.lower() not in HOP:
                    h.send_header(k, v)
            h.send_header("Transfer-Encoding", "chunked")
            h.end_headers()
            answered = True
            try:
                for chunk in r.iter_content(chunk_size=None):
                    if not chunk:
                        continue
                    first_byte = first_byte or time.time()
                    if streaming:
                        sse.feed(chunk)
                    else:
                        raw.append(chunk)
                    h.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    h.wfile.flush()
                h.wfile.write(b"0\r\n\r\n")
                h.wfile.flush()
            finally:
                r.close()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            record["error"] = "the agent closed the connection"
            h.close_connection = True
        except requests.RequestException as e:
            record["error"] = f"{type(e).__name__}: {e}"[:500]
            if answered:  # (the upstream failed mid-answer: the agent sees the answer cut short)
                h.close_connection = True
            else:
                self._reply_error(h, 502, record["error"])
        ended = time.time()
        response = sse.result() if sse.events else None
        if response is None and raw:
            try:
                response = json.loads(b"".join(raw))
            except ValueError:
                response = {"_text": b"".join(raw).decode("utf-8", "replace")[:20000]}
        model, usage = llm_usage(response)
        if not model and isinstance(request, dict):
            model = request.get("model")
        if usage and record["path"].rstrip("/").endswith(("/messages", "/chat/completions", "/responses",
                                                          "generateContent", "streamGenerateContent")):
            self.usage.add("llm", model, **usage)
        record.update(status=status, ended=round(ended, 3), latency_s=round(ended - started, 3),
                      ttft_s=round(first_byte - started, 3) if first_byte else None, model=model, usage=usage,
                      response=response, stream=bool(sse.events))
        if sse.events and sse.message is None:
            record["events"] = sse.events  # (another API's stream, as it came)
        self._write(record)

    def _reply_error(self, h, status, message):
        data = json.dumps({"type": "error", "error": {"type": "proxy_error", "message": message}}).encode()
        try:
            h.send_response(status)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(data)))
            h.end_headers()
            h.wfile.write(data)
        except OSError:
            pass

    def _write(self, record):
        with self.write_lock:
            self.calls += 1
            record["call"] = self.calls
            if self.file:
                self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
                self.file.flush()


# -- a run ----------------------------------------------------------------------------------------------------------

# The environment variables an agent reads its language model's API root from, per provider
BASE_URL = "{}_BASE_URL"


class RunTelemetry:
    """One run's usage and, when shared, its data. Use as `with RunTelemetry(...) as tel:` around the agent's work
    (inside a run through Aura, so its relay is what the proxy sends to); pass `tel.jev(client)` as the decision
    model's client. On leaving: the recording stops, the plugin gets the final usage, and (shared) telemetry/run.json
    is written and the call logs are compressed."""

    def __init__(self, link, run_dir, run_id, project, prompt, kind, share, providers=("anthropic",), info=None):
        self.link = link
        self.run_dir = run_dir
        self.dir = os.path.join(run_dir, TELEMETRY)
        self.run_id, self.project, self.prompt, self.kind = run_id, project, prompt, kind
        self.share = share
        self.providers = providers
        self.info = dict(info or {})
        self.usage = Usage()
        self.proxy = None
        self.recorder = None
        self._saved_env = {}
        self._done = threading.Event()
        self._sent_version = -1
        self.started = time.time()
        self.uid = uuid.uuid4().hex[:12]  # (the run id is a time: two machines may share one)
        self.report = None

    def __enter__(self):
        if self.share:
            os.makedirs(self.dir, exist_ok=True)  # (its being there tells the runtime to keep each decision whole)
            self.keep_memory("memory_before")
        try:
            self.proxy = LLMProxy(self.usage, os.path.join(self.dir, "llm.jsonl") if self.share else None)
            for provider in self.providers:
                self._route(provider)
        except Exception as e:  # noqa: BLE001 - the run goes on uncounted
            print(f"note: the language model's calls aren't counted: {type(e).__name__}: {e}")
        threading.Thread(target=self._tell, name="usage", daemon=True).start()
        # Every run is recorded, for the plugin's report: a shared run's with its data, another's in the run's own
        # media/ folder (never synced or sent: aura_memory.is_telemetry). Godot's report plays it as a flipbook
        from .media import Recorder

        engine = (getattr(self.link, "hello", None) or {}).get("engine")
        self.recorder = Recorder(self.link, self.dir if self.share else self.run_dir, flipbook=engine == "godot")
        self.recorder.start()
        return self

    def _route(self, provider):
        """The provider's calls through the proxy, counted: its <PROVIDER>_BASE_URL points at the proxy, which passes
        them on to where it pointed (or the provider's own API)."""
        var = BASE_URL.format(provider.upper().replace("-", "_"))
        upstream = (os.environ.get(var) or "").strip() or UPSTREAMS.get(provider)
        if not upstream:
            return
        self.proxy.route(provider, upstream)
        self._saved_env.setdefault(var, os.environ.get(var))
        os.environ[var] = self.proxy.url(provider)

    def add_provider(self, provider):
        """Count a provider the run's agent uses from now on (a run that went on with another agent)."""
        if provider in self.providers:
            return
        self.providers = (*self.providers, provider)
        if self.proxy is not None:
            try:
                self._route(provider)
            except Exception as e:  # noqa: BLE001 - its calls go on uncounted
                print(f"note: {provider}'s calls aren't counted: {type(e).__name__}: {e}")

    def recording(self):
        """The run's recording as the result tells the plugin (media.Recorder.shown), or None."""
        try:
            return self.recorder.shown() if self.recorder is not None else None
        except Exception as e:  # noqa: BLE001
            print(f"note: the recording isn't shown: {type(e).__name__}: {e}")
            return None

    def no_recording(self):
        """Why the run has no recording, in plain words (the result's `no_recording`); None when it has one."""
        if self.recorder is None:
            return "the game wasn't recorded"
        try:
            return self.recorder.missing()
        except Exception as e:  # noqa: BLE001
            return f"the recording couldn't be read ({type(e).__name__}: {e})"

    def jev(self, client):
        return CountingJev(client, self.usage) if client is not None else None

    def describer(self, agent_model=None, provider=None):
        """The run's describe_frames (autopilot/vision.py): the recording's frames, described by a vision model of
        `provider` (by default the agent's, the first of providers), through this run's proxy where it has one."""
        from .vision import Describer

        provider = provider or (self.providers[0] if self.providers else "anthropic")
        return Describer(self.recorder, provider, agent_model,
                         usage=self.usage, proxied=self.proxy.url("") if self.proxy else None,
                         log=self.dir if self.share else None)

    def _tell(self):
        """The plugin told the usage as it grows (at most every USAGE_EVERY seconds)."""
        while not self._done.wait(USAGE_EVERY):
            self.send_usage(final=False)

    def send_usage(self, final):
        if not final and self.usage.version == self._sent_version:
            return
        self._sent_version = self.usage.version
        try:
            self.link.send({"type": "usage", "final": final, "models": self.usage.snapshot()})
        except Exception:
            pass

    def __exit__(self, *exc):
        self._done.set()
        if self.recorder is not None:
            self.recorder.stop()
        for var, value in self._saved_env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        if self.proxy is not None:
            self.proxy.close()
        self.send_usage(final=True)
        print(usage_line(self.usage.snapshot()))  # (the agent service's log: what the run used, per model)
        if self.share:
            try:
                self.keep_memory("memory_after")
                self.write_manifest()
                compress(self.dir)
            except Exception as e:  # noqa: BLE001
                print(f"note: the run's data couldn't be finished: {type(e).__name__}: {e}")
        return False

    def keep_memory(self, name):
        """notes.md and lib.py as they are now, in telemetry/<name>/ (what the run found, and left)."""
        root = os.path.dirname(os.path.dirname(self.run_dir))
        for file in ("notes.md", "lib.py"):
            src = os.path.join(root, file)
            if os.path.isfile(src):
                os.makedirs(os.path.join(self.dir, name), exist_ok=True)
                shutil.copy2(src, os.path.join(self.dir, name, file))

    def write_manifest(self):
        """telemetry/run.json: what the run was, what it used, and its files (docs/run-data.md). Its being there
        says the run's data was shared: only such runs are sent (autopilot/training.py)."""
        from . import __version__, dataset

        hello = getattr(self.link, "hello", None) or {}
        report = self.report or {}
        manifest = {
            "schema": SCHEMA, "run": self.run_id, "run_uid": self.uid, "project": self.project, "kind": self.kind,
            "prompt": self.prompt,
            # (the codex the run saw, by id: sent with the run while the project's codex is still that one)
            "codex": dataset.codex_id(os.path.dirname(os.path.dirname(self.run_dir))),
            "started": round(self.started, 3), "ended": round(time.time(), 3),
            "autopilot": {"version": __version__, "python": platform.python_version(), "platform": sys.platform},
            "engine": {k: hello.get(k) for k in ("engine", "engine_version", "plugin_version", "host", "project_id")},
            "agent": {k: report.get(k) for k in ("agent", "client", "provider", "model", "fast", "turns",
                                                 "cost_usd")},
            "outcome": {k: report.get(k) for k in ("outcome", "summary", "evidence", "findings", "episodes",
                                                   "seconds", "stopped")},
            "usage": self.usage.snapshot(),
            "recording": self.recorder.summary() if self.recorder else None,
            "clock": CLOCK,
            "episodes": episode_spans(self.run_dir),
            **self.info,
        }
        with open(os.path.join(self.dir, "run.json"), "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=1, default=str)


CLOCK = ("Every `wall` (and llm.jsonl's `started`/`ended`) is Unix seconds by this computer's clock, the one clock "
         "every file shares: sync by it. `t` is the game's own time, which starts again when a level restarts. "
         "Episode n's video (episodes/<n>/recording.mp4) has time wall - its wall0 (recording.episodes).")


def episode_spans(run_dir):
    """[{episode, wall_start, wall_end, t_start, t_end, reason}] from the run's trace<n>.jsonl files (their start and
    end events), oldest first."""
    spans = []
    for name in sorted(os.listdir(run_dir)) if os.path.isdir(run_dir) else []:
        if not (name.startswith("trace") and name.endswith(".jsonl") and name[5:-6].isdigit()):
            continue
        first = last = t_start = None
        try:
            with open(os.path.join(run_dir, name), encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    first = first or e
                    last = e
                    if t_start is None and e.get("t") is not None:  # (the start event has no game time yet)
                        t_start = e["t"]
        except OSError:
            continue
        if first:
            spans.append({"episode": name[:-6], "wall_start": first.get("wall"), "wall_end": last.get("wall"),
                          "t_start": t_start, "t_end": last.get("t"),
                          "reason": last.get("reason") if last.get("kind") == "end" else None})
    spans.sort(key=lambda s: s["wall_start"] or 0)
    return spans


def compress(folder):
    """Every .jsonl in folder (not below it) gzipped in place: x.jsonl -> x.jsonl.gz."""
    for name in os.listdir(folder):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(folder, name)
        with open(path, "rb") as src, gzip.open(path + ".gz", "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        os.remove(path)
