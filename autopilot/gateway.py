"""Autopilot's model calls through Vercel AI Gateway (docs/gateway.md): one key for Jev and every language model.

With a gateway key (AI_GATEWAY_API_KEY) and runs not through Aura, the gateway is the default: Jev, the agent's
language model and the vision model that describes frames all go through it. AUTOPILOT_GATEWAY=0 turns it off, and
the old ways work as before (TYPESAFE_API_KEY and the user's own provider keys, or Aura).

    key()        the gateway key: the environment's AI_GATEWAY_API_KEY, else the settings folder's gateway.env (one line
                 AI_GATEWAY_API_KEY=...), else autopilot.env. Never written anywhere by Autopilot, never logged.
    enabled()    whether runs go through the gateway now.
    jev()        Jev's client settings through it (typesafe_sdk's TypeSafe-compatible API at <gateway>/typesafe).
    model_id()   a model as the gateway names it (claude-opus-5-5 -> anthropic/claude-opus-5.5).
    Run          inside a run: the agents' API roots and keys are the gateway's (<PROVIDER>_BASE_URL and the
                 provider's key variables, as aura.Run sets Aura's relay), put back on leaving.

The gateway speaks each provider's own API: Anthropic's Messages at <gateway>/v1/messages (Claude Code, the Claude
Agent SDK and Anthropic's SDK take <gateway> as ANTHROPIC_BASE_URL), OpenAI's chat completions and responses at
<gateway>/v1 (Codex, the vision model of other providers), and TypeSafe's at <gateway>/typesafe (Jev).
"""

import contextlib
import os

URL = "https://ai-gateway.vercel.sh"  # AUTOPILOT_GATEWAY_URL names another
KEY_ENV = "AI_GATEWAY_API_KEY"
SWITCH = "AUTOPILOT_GATEWAY"  # "0": not through the gateway, whatever key there is
FILE = "gateway.env"  # in the user's settings folder (bridge.brain_file), beside autopilot.env
JEV_PATH = "/typesafe"  # TypeSafe's API on the gateway (typesafe_sdk adds /v1/...)
OPENAI_PATH = "/v1"  # OpenAI's API on the gateway (chat completions, responses, models)

# Autopilot's model ids (agents/session.py MODELS, vision.MODEL, runtime.JEV_MODEL) as the gateway names them. The
# gateway also takes Anthropic's and Jev's own ids as they are; the others need their provider's prefix
MODELS = {
    "claude-opus-5-5": "anthropic/claude-opus-5.5",
    "gpt-6.1-sol": "openai/gpt-6.1-sol",
    "gemini-4-argon": "google/gemini-4-argon",
    "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
    "anthropic/claude-opus-5.5": "anthropic/claude-opus-5.5",
    "jev-1.13.0": "jev-1.13.0",
}
# A provider's models' prefix on the gateway (a model of Autopilot's provider ids not in MODELS)
PREFIX = {"anthropic": "anthropic", "openai": "openai", "google": "google", "deepseek": "deepseek",
          "opencode": "anthropic"}
# OpenCode's provider for the gateway (models.dev's "vercel": the AI SDK's gateway provider, with AI_GATEWAY_API_KEY),
# for every provider but Anthropic (OpenCode's Anthropic provider takes the gateway as its base URL)
OPENCODE_PROVIDER = "vercel"
# The settings a run through the gateway replaces while it plays, and puts back after (aura.RUN_ENV's)
RUN_ENV = ("TYPESAFE_BASE_URL", "TYPESAFE_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN",
           "ANTHROPIC_OAUTH_TOKEN", "AUTOPILOT_MODEL", "AUTOPILOT_OPENCODE_PROVIDER", "OPENAI_BASE_URL",
           "OPENAI_API_KEY", "CODEX_API_KEY", KEY_ENV)


def url():
    """The gateway's root, without a trailing slash."""
    return (os.environ.get("AUTOPILOT_GATEWAY_URL") or "").strip().rstrip("/") or URL


def _file_path():
    from . import bridge

    return bridge.brain_file(FILE)


def key():
    """The gateway key, or None: the environment's AI_GATEWAY_API_KEY, else gateway.env in the settings folder, else
    autopilot.env (the plugins' first-run setup's file)."""
    value = (os.environ.get(KEY_ENV) or "").strip()
    if value:
        return value
    from dotenv import dotenv_values

    for path in (_file_path(), _user_env_path()):
        try:
            value = (dotenv_values(path).get(KEY_ENV) or "").strip() if os.path.exists(path) else ""
        except (OSError, ValueError):
            value = ""
        if value:
            return value
    return None


def _user_env_path():
    from . import account

    return account._user_env_path()


def switched_off():
    return (os.environ.get(SWITCH) or "").strip().lower() in ("0", "false", "no", "off")


def enabled():
    """Whether model calls go through the gateway now: a gateway key, AUTOPILOT_GATEWAY not 0, and runs not through
    Aura (Aura's relay is the run's then)."""
    from . import aura

    return not switched_off() and not aura.enabled() and bool(key())


def mode():
    """How runs reach the models, for the account frame and the docs' words: "gateway" or None."""
    return "gateway" if enabled() else None


def model_id(model, provider=None):
    """model as the gateway names it: MODELS', else the provider's prefix added (a model with one kept as it is)."""
    model = str(model or "").strip()
    if not model or "/" in model:
        return model
    if model in MODELS:
        return MODELS[model]
    if model.startswith("claude-"):
        return f"anthropic/{model}"
    prefix = PREFIX.get(provider or "")
    return f"{prefix}/{model}" if prefix else model


def jev():
    """typesafe_sdk.TypeSafeClient's api_key and base_url through the gateway: {} when it isn't used."""
    if not enabled():
        return {}
    return {"api_key": key(), "base_url": url() + JEV_PATH}


def opencode_provider(provider):
    """The OpenCode provider that reaches provider's models through the gateway: Anthropic's own (its base URL the
    gateway's), else the gateway's (OPENCODE_PROVIDER)."""
    return "anthropic" if provider in ("anthropic", "opencode") else OPENCODE_PROVIDER


def agent_env(the_key=None):
    """The environment variables a run through the gateway sets: Jev's (TypeSafe's API), Anthropic's (Claude Code,
    the Claude Agent SDK, OpenCode's Anthropic provider, the vision model) and OpenAI's (Codex) API roots, and the
    gateway key as each one's key."""
    the_key = the_key or key()
    root = url()
    return {"TYPESAFE_BASE_URL": root + JEV_PATH, "TYPESAFE_API_KEY": the_key,
            "ANTHROPIC_BASE_URL": root, "ANTHROPIC_API_KEY": the_key,
            "OPENAI_BASE_URL": root + OPENAI_PATH, "OPENAI_API_KEY": the_key, "CODEX_API_KEY": the_key,
            KEY_ENV: the_key}


class Run:
    """One run through the gateway. Use as `with gateway.Run():` around the play: inside, Jev and the agents use the
    gateway (agent_env, and OpenCode on another provider than Anthropic switched to the gateway's, its model named as
    the gateway names it); on leaving, the settings are put back."""

    def __init__(self, the_key=None):
        self.key = the_key or key()
        self._saved = None

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in RUN_ENV}
        for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN"):
            os.environ.pop(k, None)  # (else a client may prefer the user's own sign-in to the gateway key)
        os.environ.update(agent_env(self.key))
        provider = (os.environ.get("AUTOPILOT_OPENCODE_PROVIDER") or "").strip().lower()
        if provider and opencode_provider(provider) != "anthropic":
            from .agents import session

            os.environ["AUTOPILOT_OPENCODE_PROVIDER"] = OPENCODE_PROVIDER
            os.environ["AUTOPILOT_MODEL"] = model_id(session.model(provider), provider)
        return self

    def __exit__(self, *exc):
        for k, v in (self._saved or {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def run():
    """A Run when model calls go through the gateway now, else a context that does nothing."""
    return Run() if enabled() else contextlib.nullcontext()
