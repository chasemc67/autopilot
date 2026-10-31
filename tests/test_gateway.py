"""Vercel AI Gateway (autopilot/gateway.py): the key, when it is used, and what each model call does with it. No
network: the gateway is a stand-in on this machine where a call is made."""

import json
import os
from types import SimpleNamespace

import pytest

from autopilot import account, cli, gateway, redact, runtime, telemetry, vision
from autopilot.agents import claude_code, opencode, session

KEY = "vck_test0123456789abcdefghijklmnopqrstuv"


@pytest.fixture
def no_model_env(monkeypatch):
    for name in gateway.RUN_ENV + ("AUTOPILOT_AURA_URL", "AUTOPILOT_JEV_STANDIN", "AUTOPILOT_VISION_MODEL",
                                   "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def with_key(monkeypatch, no_model_env):
    monkeypatch.setenv(gateway.KEY_ENV, KEY)
    return KEY


# -- the key -----------------------------------------------------------------------------------------------------------

def test_the_key_is_the_environments_then_gateway_env_then_autopilot_envs(tmp_path, monkeypatch, no_model_env):
    assert gateway.key() is None and not gateway.enabled()
    (tmp_path / "autopilot.env").write_text("AI_GATEWAY_API_KEY=vck_from_autopilot_env_000\n")
    assert gateway.key() == "vck_from_autopilot_env_000"
    (tmp_path / "gateway.env").write_text("AI_GATEWAY_API_KEY=vck_from_gateway_env_0000\n")
    assert gateway.key() == "vck_from_gateway_env_0000"
    monkeypatch.setenv(gateway.KEY_ENV, KEY)
    assert gateway.key() == KEY


def test_the_gateway_is_the_default_with_a_key_unless_turned_off_or_through_aura(monkeypatch, with_key):
    assert gateway.enabled() and gateway.mode() == "gateway"
    monkeypatch.setenv(gateway.SWITCH, "0")
    assert not gateway.enabled() and gateway.jev() == {}
    monkeypatch.setenv(gateway.SWITCH, "1")
    monkeypatch.setenv("AUTOPILOT_AURA_URL", "https://aura.example")  # (Aura's relay plays the run)
    assert not gateway.enabled()


def test_models_are_named_as_the_gateway_names_them():
    assert gateway.model_id("claude-opus-5-5") == "anthropic/claude-opus-5.5"
    assert gateway.model_id("gpt-6.1-sol") == "openai/gpt-6.1-sol"
    assert gateway.model_id("gemini-4-argon") == "google/gemini-4-argon"
    assert gateway.model_id("deepseek-v4-pro") == "deepseek/deepseek-v4-pro"
    assert gateway.model_id("anthropic/claude-opus-5.5") == "anthropic/claude-opus-5.5"
    assert gateway.model_id("claude-sonnet-5") == "anthropic/claude-sonnet-5"
    assert gateway.model_id("gpt-7", "openai") == "openai/gpt-7"
    assert gateway.model_id("mystery") == "mystery"


# -- Jev ---------------------------------------------------------------------------------------------------------------

def test_jev_is_reached_through_the_gateway_with_its_key(monkeypatch, with_key):
    seen = {}

    class Client:
        def __init__(self, **kw):
            seen.update(kw)

    import typesafe_sdk

    monkeypatch.setattr(typesafe_sdk, "TypeSafeClient", Client)
    runtime.jev_client(warm=False)
    assert seen["api_key"] == KEY and seen["base_url"] == "https://ai-gateway.vercel.sh/typesafe"
    assert seen["model"] == runtime.JEV_MODEL
    monkeypatch.setenv("AUTOPILOT_JEV_MODEL", "jev-1.14.0")  # (the override still picks the version)
    seen.clear()
    runtime.jev_client(warm=False)
    assert seen["model"] == "jev-1.14.0"
    monkeypatch.setenv(gateway.SWITCH, "0")  # (off: TypeSafe's own, as the SDK reads the environment)
    seen.clear()
    runtime.jev_client(warm=False)
    assert "api_key" not in seen and "base_url" not in seen
    monkeypatch.setenv("AUTOPILOT_JEV_STANDIN", "random")  # (the stand-in is still the stand-in)
    assert isinstance(runtime.jev_client(warm=False), runtime.StandInJev)


def test_jevs_cost_as_the_gateway_says_it_is_counted():
    body = {"model": "jev-1.13.0", "usage": {"input_tokens": 308, "output_tokens": 31},
            "provider_metadata": {"gateway": {"cost": "0.000012936", "generationId": "gen_1"}}}
    answer = SimpleNamespace(model="jev-1.13.0", usage=SimpleNamespace(input_tokens=308, output_tokens=31),
                             raw_http_response=SimpleNamespace(content=json.dumps(body).encode()))
    usage = telemetry.Usage()
    jev = telemetry.CountingJev(SimpleNamespace(system_one=lambda **kw: answer), usage)
    jev.system_one(state={}, questions={})
    jev.system_one(state={}, questions={})
    (m,) = usage.snapshot()
    assert (m["kind"], m["calls"], m["input"], m["output"]) == ("decision", 2, 616, 62)
    assert m["cost_usd"] == pytest.approx(0.000025872)
    plain = SimpleNamespace(model="jev-1.13.0", usage=None, raw=None)  # (TypeSafe's own: no cost said)
    assert telemetry.decision_cost(plain) is None


def test_a_language_models_cost_as_the_gateway_says_it_is_counted():
    body = {"model": "openai/gpt-6.1-sol", "usage": {"prompt_tokens": 13, "completion_tokens": 5, "cost": 7.6e-05,
                                                     "cache_creation_input_tokens": 0}}  # (as the gateway says it)
    model, usage = telemetry.llm_usage(body)
    assert model == "openai/gpt-6.1-sol" and usage["cost_usd"] == 7.6e-05 and usage["input"] == 13
    u = telemetry.Usage()
    u.add("llm", model, **usage)
    assert u.snapshot()[0]["cost_usd"] == 7.6e-05
    assert "cost_usd" not in telemetry.llm_usage({"usage": {"prompt_tokens": 1}})[1]


# -- a run's settings --------------------------------------------------------------------------------------------------

def test_a_run_through_the_gateway_points_every_agent_at_it_and_puts_the_settings_back(monkeypatch, with_key):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-users-own")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "users-sign-in")
    with gateway.run() as run:
        assert isinstance(run, gateway.Run)
        assert os.environ["ANTHROPIC_BASE_URL"] == "https://ai-gateway.vercel.sh"
        assert os.environ["ANTHROPIC_API_KEY"] == KEY and "ANTHROPIC_AUTH_TOKEN" not in os.environ
        assert os.environ["OPENAI_BASE_URL"] == "https://ai-gateway.vercel.sh/v1"
        assert os.environ["OPENAI_API_KEY"] == os.environ["CODEX_API_KEY"] == KEY
        assert os.environ["TYPESAFE_BASE_URL"] == "https://ai-gateway.vercel.sh/typesafe"
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-users-own"
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "users-sign-in"
    assert "ANTHROPIC_BASE_URL" not in os.environ and "OPENAI_BASE_URL" not in os.environ


def test_no_run_through_the_gateway_without_a_key(no_model_env):
    with gateway.run() as run:
        assert run is None and "ANTHROPIC_BASE_URL" not in os.environ


def test_opencode_plays_other_providers_on_the_gateways_provider(monkeypatch, with_key):
    monkeypatch.setenv("AUTOPILOT_OPENCODE_PROVIDER", "google")
    with gateway.Run():
        assert opencode.current_provider() == "vercel"
        assert session.model("vercel") == "google/gemini-4-argon"
        env = opencode.host_env(os.path.join(os.environ.get("TMPDIR", "/tmp"), "gw-test"))
        assert env["AI_GATEWAY_API_KEY"] == KEY and "ANTHROPIC_API_KEY" not in env  # (only its own key goes)
    assert opencode.current_provider() == "google" and "AUTOPILOT_MODEL" not in os.environ
    monkeypatch.setenv("AUTOPILOT_OPENCODE_PROVIDER", "anthropic")  # (Anthropic's own, its base URL the gateway's)
    with gateway.Run():
        assert opencode.current_provider() == "anthropic"
        assert opencode.base_url("anthropic") == "https://ai-gateway.vercel.sh/v1"


def test_claude_code_gets_the_gateways_root_and_key_and_no_other_secret(monkeypatch, with_key, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-should-not-pass")
    with gateway.Run():
        env = claude_code._env(str(tmp_path))
        # (blanked: every secret but the Anthropic key; the base URL isn't one, so it is inherited as it is)
        assert env.get("ANTHROPIC_API_KEY") == KEY and env.get(gateway.KEY_ENV) == ""
        assert env.get("TYPESAFE_API_KEY") == "" and "ANTHROPIC_BASE_URL" not in env
        assert os.environ["ANTHROPIC_BASE_URL"] == "https://ai-gateway.vercel.sh"


def test_codex_is_pointed_at_the_gateway(with_key):
    from autopilot.agents import codex

    with gateway.Run():
        assert codex.api_key() == KEY
        config = codex.thread_config("spec.json", 60, os.environ.get("OPENAI_BASE_URL"))
        assert config["openai_base_url"] == "https://ai-gateway.vercel.sh/v1"
        env = codex.app_env("/h", "/tmp/w")  # (Codex signs in with the key itself: login_api_key)
        assert KEY not in env.values() and not any(k.startswith("OPENAI_") for k in env)


# -- what the user is told --------------------------------------------------------------------------------------------

def test_no_typesafe_key_is_asked_for_with_a_gateway_key(monkeypatch, no_model_env):
    why = cli.decision_key_missing()
    assert "AI_GATEWAY_API_KEY=" in why and "TYPESAFE_API_KEY=" in why and "Don't paste the key" in why
    monkeypatch.setenv(gateway.KEY_ENV, KEY)
    assert cli.decision_key_missing() is None
    monkeypatch.setenv(gateway.SWITCH, "0")
    assert cli.decision_key_missing() is not None


def test_the_account_frame_says_the_gateway_and_shows_none_of_its_key(with_key):
    frame = account.state()
    assert frame["mode"] == "own_keys" and frame["via"] == "gateway"
    assert frame["keys"] == {"decision": "••••", "llm": "••••", "gateway": "••••"}
    assert KEY[-4:] not in json.dumps(frame, ensure_ascii=False)


def test_the_key_is_taken_out_of_shared_data_and_what_the_plugin_is_sent(tmp_path, no_model_env):
    (tmp_path / "gateway.env").write_text(f"AI_GATEWAY_API_KEY={KEY}\n")  # (not in the environment)
    r = redact.Redactor()
    assert KEY not in r.text(f"key {KEY} in a log") and r.counts["secret"] >= 1
    assert "[secret]" in redact.Redactor(env={}).text(f"x vck_{'a' * 30} y")  # (by its shape too)
    assert session.scrub({"summary": f"the key is {KEY}"}) == {"summary": "the key is [secret]"}


# -- the vision model --------------------------------------------------------------------------------------------------

def test_frames_are_described_through_the_gateway(monkeypatch, with_key):
    from test_vision import FakeRecorder, Upstream, frames

    up = Upstream(refuse=("google/gemini-mystery",))
    try:
        monkeypatch.setenv("AUTOPILOT_GATEWAY_URL", up.url)
        (wire, root, key, model, _), why = vision.route(vision.GATEWAY, "claude-opus-5-5")
        assert (wire, root, key, model, why) == ("anthropic", up.url, KEY, "claude-opus-5-5", None)
        (wire, root, _, model, path), _ = vision.route(vision.GATEWAY, "gpt-6.1-sol")
        assert (wire, root, model, path) == ("openai", up.url + "/v1", "openai/gpt-6.1-sol", "/chat/completions")
        assert vision.route(vision.GATEWAY, "deepseek-v4-pro")[0][3] == vision.MODEL  # (it can't look: Claude does)

        rec = FakeRecorder()
        frames(rec)
        usage = telemetry.Usage()
        d = vision.Describer(rec, vision.GATEWAY, "gpt-6.1-sol", usage=usage)
        text, ok = d.describe(100.0, 104.0, ["What opens?"], count=3)
        assert ok and "A door opens." in text
        path, headers, body = up.seen[-1]
        assert path == "/v1/chat/completions" and headers["authorization"] == f"Bearer {KEY}"
        assert body["model"] == "openai/gpt-6.1-sol" and "max_tokens" in body
        assert usage.snapshot()[0]["model"] == "openai/gpt-6.1-sol"

        # A model the gateway refuses gives way to Claude, on Anthropic's wire, for the rest of the run
        refused = vision.Describer(rec, vision.GATEWAY, "google/gemini-mystery")
        text, ok = refused.describe(100.0, 104.0, ["What moves?"], count=2)
        assert ok and "arm swings" in text and refused.route[0] == "anthropic" and refused.route[3] == vision.FALLBACK
        assert up.seen[-1][0] == "/v1/messages" and up.seen[-1][1]["x-api-key"] == KEY
    finally:
        up.close()


def test_no_gateway_route_without_a_key(no_model_env):
    assert vision.route(vision.GATEWAY) == (None, "there is no gateway key")


def test_the_trace_and_the_log_say_which_jev_played_and_what_the_run_used(monkeypatch, with_key):
    from autopilot import sandbox

    assert runtime.jev_route() == {"model": runtime.JEV_MODEL, "via": "gateway"}
    assert sandbox.child_env("/m")[runtime.JEV_VIA] == "gateway" and KEY not in sandbox.child_env("/m").values()
    monkeypatch.setenv("AUTOPILOT_SANDBOX", "child")  # (the child holds no key: its parent says)
    monkeypatch.delenv(gateway.KEY_ENV)
    monkeypatch.setenv(runtime.JEV_VIA, "gateway")
    assert runtime.jev_route()["via"] == "gateway"
    u = telemetry.Usage()
    u.add("decision", "jev-1.13.0", input=300, output=30, cost_usd=0.0000126)
    assert telemetry.usage_line(u.snapshot()) == ("Usage: decision jev-1.13.0: 1 calls, 300 in / 30 out tokens, "
                                                  "$0.000013")
