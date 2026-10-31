"""Checks Autopilot's models on Vercel AI Gateway (docs/gateway.md): which are listed, and a tiny live call to each.

    python tools/gateway_check.py [--out results.json] [--image frame.jpg] [--jev-calls 20]

The key comes from autopilot.gateway.key() (the environment, the settings folder's gateway.env, autopilot.env). It is
never printed: everything printed or written has it replaced by [secret]. Calls are tiny (max_tokens 20, one small
picture for the models that take pictures, a one-question Jev call): a whole check costs well under a cent.
"""

import argparse
import base64
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from autopilot import gateway  # noqa: E402

TEXT = "Reply with the single word: ok"
SEE = "In at most 8 words: what is in this picture?"
# (autopilot model id, gateway id, wire, takes pictures)
CHECKS = [
    ("claude-opus-5-5", "claude-opus-5-5", "anthropic", True),
    ("claude-opus-5-5", "anthropic/claude-opus-5.5", "anthropic", True),
    ("claude-opus-5-5", "anthropic/claude-opus-5.5", "chat", True),
    ("gpt-6.1-sol", "openai/gpt-6.1-sol", "chat", True),
    ("gpt-6.1-sol", "gpt-6.1-sol", "chat", False),
    ("gpt-6.1-sol", "openai/gpt-6.1-sol", "responses", False),
    ("gpt-6.1-sol", "gpt-6.1-sol", "responses", False),
    ("gemini-4-argon", "google/gemini-4-argon", "chat", True),
    ("deepseek-v4-pro", "deepseek/deepseek-v4-pro", "chat", False),
    ("anthropic/claude-opus-5.5", "anthropic/claude-opus-5.5", "chat", False),
]


def clean(value, secret):
    text = json.dumps(value, default=str)
    return json.loads(text.replace(secret, "[secret]")) if secret else value


def anthropic_call(root, key, model, image, effort=True):
    content = [{"type": "text", "text": SEE if image else TEXT}]
    if image:
        content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image}})
    body = {"model": model, "max_tokens": 20, "messages": [{"role": "user", "content": content}]}
    if effort:
        body["output_config"] = {"effort": "low"}
    r = requests.post(f"{root}/v1/messages", json=body, timeout=60,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"_text": r.text[:300]}
    text = "".join(b.get("text", "") for b in data.get("content") or [] if isinstance(b, dict))
    return r, data, text


def chat_call(root, key, model, image):
    content = [{"type": "text", "text": SEE if image else TEXT}]
    if image:
        content.insert(0, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}})
    r = requests.post(f"{root}/v1/chat/completions", timeout=60, headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "max_tokens": 20, "messages": [{"role": "user", "content": content}]})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"_text": r.text[:300]}
    text = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "") if isinstance(data, dict) \
        else ""
    return r, data, text


def responses_call(root, key, model, image):
    r = requests.post(f"{root}/v1/responses", timeout=60, headers={"Authorization": f"Bearer {key}"},
                      json={"model": model, "max_output_tokens": 20, "input": TEXT})
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {"_text": r.text[:300]}
    text = ""
    for item in (data.get("output") or []) if isinstance(data, dict) else []:
        for c in item.get("content") or []:
            text += c.get("text", "") if isinstance(c, dict) else ""
    return r, data, text


def jev_calls(n, model):
    from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

    client = TypeSafeClient(model=model, timeout=5.0, retry=RetryPolicy(max_retries=0, timeout=5.0),
                            compression=False, **gateway.jev())
    questions = {"pick": Choice(instructions="Choose the option that best serves `goal`.",
                                criteria={"coin": "walk to the nearest coin", "wait": "stand still"})}
    out = {"model": model, "latencies_ms": [], "errors": [], "answers": [], "usage": None}
    for i in range(n):
        t0 = time.monotonic()
        try:
            r = client.system_one(state={"goal": "collect coins", "situation": {"coins_left": 5 - i % 5}},
                                  questions=questions)
            out["latencies_ms"].append(round((time.monotonic() - t0) * 1000))
            pick = r.choices["pick"]
            out["answers"].append({"choice": pick.choice, "probabilities": dict(pick.probabilities or {})})
            u = getattr(r, "usage", None)
            out["usage"] = u.model_dump() if hasattr(u, "model_dump") else str(u)
            out["model_answered"] = getattr(r, "model", None)
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{type(e).__name__}: {str(e)[:200]}")
        time.sleep(0.2)
    lat = out["latencies_ms"]
    if lat:
        out["p50_ms"] = statistics.median(lat)
        out["p90_ms"] = sorted(lat)[max(0, int(len(lat) * 0.9) - 1)]
        out["first_ms"] = lat[0]
    t0 = time.monotonic()
    try:
        client.models.list(timeout=5.0, retry=RetryPolicy(max_retries=0, timeout=5.0))
        out["keep_warm_models_list"] = f"ok in {round((time.monotonic() - t0) * 1000)} ms"
    except Exception as e:  # noqa: BLE001
        out["keep_warm_models_list"] = f"{type(e).__name__}: {str(e)[:200]}"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--image", default=os.path.join(os.path.dirname(__file__), "..", "docs", "roblox-live-evidence",
                                                    "05-e2e-verify-recording-frame.jpg"))
    ap.add_argument("--jev-calls", type=int, default=20)
    ap.add_argument("--skip-llm", action="store_true")
    args = ap.parse_args()
    key = gateway.key()
    if not key:
        sys.exit("no gateway key (AI_GATEWAY_API_KEY, gateway.env, autopilot.env)")
    root = gateway.url()
    with open(args.image, "rb") as f:
        image = base64.standard_b64encode(f.read()).decode("ascii")
    results = {"gateway": root, "when": time.strftime("%Y-%m-%d %H:%M:%S %Z")}

    r = requests.get(f"{root}/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    ids = sorted(m.get("id") for m in (r.json().get("data") or [])) if r.ok else []
    results["models_listed"] = len(ids)
    wanted = sorted({g for _, g, _, _ in CHECKS} | set(gateway.MODELS.values()) | {"typesafe-ai/jev", "jev-1.14.0"})
    results["listed"] = {m: m in ids for m in wanted}
    results["jev_ids_listed"] = [m for m in ids if "jev" in m]

    calls = []
    for ours, theirs, wire, sees in ([] if args.skip_llm else CHECKS):
        for with_image in ((False, True) if sees else (False,)):
            t0 = time.monotonic()
            try:
                if wire == "anthropic":
                    resp, data, text = anthropic_call(root, key, theirs, image if with_image else None)
                elif wire == "chat":
                    resp, data, text = chat_call(root, key, theirs, image if with_image else None)
                else:
                    resp, data, text = responses_call(root, key, theirs, None)
                row = {"ours": ours, "gateway_id": theirs, "wire": wire, "image": with_image,
                       "status": resp.status_code, "ms": round((time.monotonic() - t0) * 1000),
                       "text": text.strip()[:80], "usage": (data or {}).get("usage"),
                       "model_answered": (data or {}).get("model"),
                       "generation_id": resp.headers.get("x-generation-id") or resp.headers.get("x-vercel-id"),
                       "error": None if resp.ok else str(data)[:300]}
            except Exception as e:  # noqa: BLE001
                row = {"ours": ours, "gateway_id": theirs, "wire": wire, "image": with_image, "status": None,
                       "error": f"{type(e).__name__}: {str(e)[:200]}"}
            calls.append(row)
            print(json.dumps(clean(row, key)))
    if not args.skip_llm:
        # Anthropic's wire without output_config (in case the gateway refuses it)
        resp, data, text = anthropic_call(root, key, "claude-opus-5-5", None, effort=False)
        calls.append({"ours": "claude-opus-5-5", "gateway_id": "claude-opus-5-5", "wire": "anthropic (no effort)",
                      "image": False, "status": resp.status_code, "text": text.strip()[:80]})
    results["calls"] = calls
    if args.jev_calls:
        results["jev"] = [jev_calls(args.jev_calls, m) for m in ("jev-1.13.0", "typesafe-ai/jev-1.13.0")]
        for j in results["jev"]:
            print(json.dumps(clean({k: v for k, v in j.items() if k != "answers"}, key)))
    results = clean(results, key)
    print(json.dumps({"listed": results["listed"], "jev_ids_listed": results["jev_ids_listed"],
                      "models_listed": results["models_listed"]}, indent=1))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=1)


if __name__ == "__main__":
    main()
