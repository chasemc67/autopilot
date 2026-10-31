"""One request on one game, as every agent sees it: the instructions, the briefing, and the tools' bodies.

An agent (autopilot/agents/opencode.py or claude_code.py) researches the game's
codex, probes the live game, writes harness.py for the request, runs it as episodes in which Jev decides, reads the
traces, and revises the harness until the request is settled. Then it reports. The agents differ only in how they
are run and how they reach the tools; what the tools do lives here, in Session. See docs/architecture.md.
"""

import ast
import contextlib
import io
import json
import math
import os
import re
import sys
import threading
import time
import traceback

from .. import codex as codexmod
from .. import engines, media, runtime, vision
from .. import levels as levelsmod
from ..kit import control
from ..telemetry import episode_spans
from ..words import plain_words
from . import feed

# Settings shared by every agent (environment variables override them)
# The agent's language model when AUTOPILOT_MODEL names none: its provider's (model() reads them before each run: the
# plugins' first-run setup may change the provider, in autopilot.env, while the agent service runs)
# Each LLM provider's model (OpenCode's provider ids; "opencode" is OpenCode Zen, "google" Gemini)
MODELS = {"anthropic": "claude-opus-5-5", "openai": "gpt-6.1-sol", "opencode": "claude-opus-5-5",
          "openrouter": "anthropic/claude-opus-5.5", "deepseek": "deepseek-v4-pro", "google": "gemini-4-argon"}
EFFORT = os.environ.get("AUTOPILOT_EFFORT", "medium")  # how hard the model thinks per turn
TIMEOUT = 30 * 60.0  # one request's whole budget, in seconds, when neither the run nor AUTOPILOT_TIMEOUT says
MAX_EPISODE = 360.0  # the longest episode (seconds of play) one run_harness may ask for
MIN_JEV_DECISIONS = 3  # a report needs an episode in which Jev chose among 2+ options at least this often
PLAY_GRACE = 90.0  # once an exploration's play time is up, the seconds its agent has to report before the run ends
# The longest one probe may run (wall seconds). A probe that drove the player through a whole goal, or read every
# unit of a strategy game, ran 8-9 minutes in trials with the agent waiting on it. Past this the keys are let go, the
# game paused, and what it printed so far comes back with a note. AUTOPILOT_PROBE_SECONDS overrides it
PROBE_SECONDS = float(os.environ.get("AUTOPILOT_PROBE_SECONDS") or 120.0)
# The seconds a run's reading and probing have in all before its first episode: the plugins' Analyze and Probe
# stages, however often the run goes back and forth between them (writing the harness doesn't use them). They count
# from when the game is ready (cli.prepare: an editor's play session still starting doesn't use them). Once they are up
# the run builds the harness: probes are closed, and reading and probing are Build, until an episode has played.
# AUTOPILOT_RESEARCH_SECONDS overrides it (research_seconds)
RESEARCH_SECONDS = 10 * 60.0
RESEARCH_STAGES = ("analyze", "probe")  # (the feed's stages that use them)

# The progress lines the plugins' tabs show as stages (in their own words), besides "Trying the harness (run N)"
READING = "Reading the game's code"
PROBING = "Trying things in the game"
WRITING = "Writing the harness"
RESULTS = "Looking at the results"  # an episode ended: the implementer works on how it went, until it plays again
# The stages of the activity feed (docs/protocol.md, `activity`), as the progress lines name them; "play" is "Trying
# the harness (run N)" and "report" the accepted report. The one place that maps them: stage_of()
STAGES = {READING: "analyze", PROBING: "probe", WRITING: "build", RESULTS: "results"}
PLAYING = "Trying the harness"  # (how the progress line of an episode starts)
PROBE_RESULT = 30000  # the most characters an activity event's detail carries (a probe's output, a file's head)
# The kinds of activity event whose call the agent makes and whose result the tool's body sends later, with `ends`
# naming the call's id (Session.activity)
CALL_KINDS = ("probe", "episode", "look")

PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # the autopilot package
KIT_DOC = os.path.join(PACKAGE, "KIT.md")
HARNESS_DOC = os.path.join(PACKAGE, "kit", "harness.py")

SYSTEM = """You write the harness that lets Jev, a fast decision model, play a video game made in {engine}. You are
inside the game's editor plugin: you can read the game's code (its codex) and act on the live game through tools.

Jev answers typed questions in ~0.1-0.3 s: given a state (JSON facts) it picks one of a set of options, with
probabilities. People made Jev play Doom (monsters as distance + bearing; ATTACK/TURN_LEFT/TURN_RIGHT at 7 Hz),
Tetris (it ranks every legal placement, described in words), Pac-Man (one block of facts per legal direction at each
junction) and Flappy Bird (FLAP/WAIT 20 times a second) by writing a harness by hand: code computes the facts and the
legal options and executes the chosen option; Jev decides. You write that harness, for this game and this request,
from the game's code and from what you see it do.

Your working directory is this game's folder:
  codex/       the game's code and data as text (start with codex/README.md; level.txt is the level running now)
  notes.md     what earlier requests on this game learned (verified facts, controls, pitfalls), if any
  lib.py       harness code earlier requests found reusable for this game (importable as `import lib`), if any
  runs/        earlier runs: their harness.py, trace summaries and reports (reuse what worked)
  {run}/  this request's folder: write harness.py here

How to work:
0. The level: when the request means a level other than the one playing (the briefing's "The level" lists the
   project's), call restart_level with it before anything else.
1. The request comes with what you would read first: notes.md, lib.py, earlier runs' reports, what changed in the
   codex since the last run, codex/README.md, where the request's words appear in the codex, and what the live game
   shows now (looked at just before you started). Don't read or probe those again. What notes.md, lib.py and earlier
   harnesses say about code that changed may be out of date: check it before you rely on it. Fix the note in notes.md
   or the helper in lib.py where it no longer holds; where it still holds, say so there. Until one or the other, the
   change is listed again for every later run. Research the codex for this request: how the game works where the
   request touches it (the code paths of the mechanics involved, which inputs drive them and what they need: range,
   aim, held item, mode, cooldowns; what counts as done; what can kill or stop the player). Be targeted: grep, then
   read what matters (several files in one turn). Don't read the whole codex. The data tables are read fastest with
   the research helpers in a probe (`rows`, `row`, `cell`, `grep`: see Research helpers below) rather than by
   grepping their text.{agents} Never wait for an answer that is still coming: go on probing; what you learn
   in the game outranks what is read.
2. Check what you rely on in the live game with `probe` (probes check assumptions; they are not how the request is
   settled: the outcome must come from run_harness episodes, in which Jev plays) (the game is paused between your tool
   calls and runs only inside them): find the actors, read their properties, press an input and read what changed. The
   code says what should happen; the probe says what does. Each probe is a round trip: put the checks you can already
   name into one probe (print each result, wrap a check that may fail in try/except so the rest still run) rather than
   one probe per question. A probe runs at most {probe_seconds:.0f} s, then what it printed so far comes back.
   Reading the game and probing it have {research_minutes:g} minutes in all before the first episode (writing the
   harness doesn't use them; every tool result says what is left). Once they are up, probes are closed until an
   episode has played: write the harness from what you know by then and run it, and spend the research on what the
   harness can't do without.
   Research in probes may change the game directly, to get to a moment fast and measure it: teleport the player
   (`teleport`), put what it needs in its hands or in reach (the game's own give/spawn/debug functions through
   `game.call` or `call_any`; many games have a debug or cheat component: grep the codex for one), make it
   invulnerable, spawn or remove actors. Prove the chain before you write the harness: every step the request
   takes, the last one included, tried once in a probe, with the numbers that decide it (reach, damage per hit, hits
   to kill, damage taken, time, what the UI needs, where the targets are and whether they can be reached). A request
   whose last step is a fight: `fight_trial` against the real target first. What the chain shows impossible as the
   player starts (too weak, too far, not reachable) is what the plan must get around (gear, a level, another
   instance, a route): find that now, not in episodes. A fight the player loses straight on is won by a tactic,
   and the tactic is found in probes before the harness is written, never by playing the lost fight again: measure
   the enemy first (`enemy_profile`: its speed, reach, attack period, damage and regeneration against the player's;
   does it come at all, or wait until hit?), then try the tactics the numbers allow, each in a probe until one wins:
   strike-and-retreat (`strike_and_retreat` with the measured reach and recovery: one hit, out of its reach while
   it recovers, back in), the player's defensive inputs from the controls (a block, a dodge, a guard) against real
   hits, range, terrain (a height it can't climb), another instance or several at once, and what makes the player
   stronger (points, gear, a level). The kill step then runs the winning tactic with the kit's controller (its
   numbers from the measurement) or a reflex (see Reacting in time), never a plain melee it has seen lose. Scale the
   research to the request. A request of one step (a claim about one mechanic: "verify that X does Y"; a goal of one
   action) gets one probe: do the step once, read the numbers that settle it, and write the harness; no chain, no
   fight measurement unless the claim is about a fight, no plan, and run_harness straight away (check_harness is for
   a plan's harness). The chain rule, the plan and check_harness are for requests of several steps. A direct change
   in a probe is listed in the result as a research change; run_harness restarts the level by default, so play
   starts clean of it.
3. Write {run}/harness.py (a subclass of autopilot.kit.Harness; see the kit below). Build executors from the kit's
   controllers (walk_to, look_at, jump_to, melee, keep_away, track...): they are tested; write your own movement or
   aiming only where none fits. A request with more than one step gets a `plan` (kit.Step list, in order, each with
   a done() read from the game's own state and the names of the options legal in it; see The plan below): the
   runtime offers Jev the current step's options only and tells it where the plan stands, so play can't wander back
   to steps already done, and it ends an episode that stands still (stall_seconds) as STALLED instead of letting it
   run on. Before the first run_harness, `check_harness()` in a probe: it loads the harness, runs setup, the first
   observation (facts, status, the plan's step, the options) and each option's executor once without Jev, and says
   what failed. Then `run_harness`, and read the summary: which options worked, which failed and why, errors, how
   the situation went, where the plan got. Fix and run again. Small, checked steps beat a big untested harness. When
   the game seemed to do something wrong in the episode (an input with no effect, a value that wouldn't change) and
   this run can look at the picture, describe_frames that moment before you probe: one look often shows a cause in
   the scene (something in the way, a menu open, the player not where the numbers put it) that rounds of probes
   would only circle.
4. Steer yourself instead of starting over. An episode that ends STALLED (or that you see is going nowhere) leaves
   the game paused where play is. Don't rebuild the harness or restart the level: find out what blocked it
   (describe_frames the last seconds; a probe of where the player is and what is around it), revise the plan's
   continuation in harness.py (the steps from here on, an option's approach, a step that gets the player out of the
   spot), and go on with run_harness(restart=False): play continues from that state, the steps done stay done (the
   runtime hands resume() the progress). Restart only when the state is lost (dead with no respawn, back in a menu)
   or the harness's setup is wrong. Between an episode and its continuation a probe only looks: a direct change
   there (teleporting the player out of the spot, giving it the item) would play the game for Jev, and counts as a
   change to the play in the report.
5. When the request is settled (the goal reached; a claim shown true or false; the exploration done) or more tries
   won't help, call `report` right away: an episode that settles it is enough (don't polish the harness after it).
   Every tool result tells the time used of your budget. Before that, update notes.md with verified facts a later
   request on this game needs (controls, mechanics, numbers, pitfalls; short lines, no guesses) and move reusable
   harness code into lib.py.

The request is one of:
- a goal ("repair the X", "reach the end", "win a round"): make Jev achieve it in play. Report achieved or
  not, with the evidence from the run in which it was achieved.
- a claim to verify ("verify that ..."): make Jev play so the claim is tested in the game, and measure what happens
  (put the measurements in Status.evidence). The verdict rests on what was observed in play, never on code alone:
  CONFIRMED (shown true), DENIED (shown false: give the observation that contradicts it) or INCONCLUSIVE.
  Code reading tells you where to look and what to measure; a bug in the code is only a lead until play shows it.
  A claim that something CAN happen ("the player can reach/climb/open X") is shown true by doing it once; the harness
  failing to do it is not a contradiction (it is as likely the harness): keep improving the attempt (other
  approaches, other instances, the kit's controllers), and say DENIED only if the game's own numbers prove it
  impossible (say which numbers), else INCONCLUSIVE.
- an exploration ("find bugs in ...", "try everything in ..."): options covering the mechanics broadly (prefer what
  hasn't been tried, edge cases, limits), and report what looked wrong with the observations. A finding is something
  the game was seen to do wrong (a value past its limit, a rule broken, a state that contradicts another). That the
  harness failed to do something ("X can't be reached") is not a finding: it is as likely the harness's limit. Report
  it only when the game's own numbers prove it impossible, and then say which numbers.
  An exploration asked for N minutes of play has only those: they count from its first episode, episodes end
  when they are up, and the run ends soon after, so spend them playing and report as they run out.

Rules:
- In play (run_harness episodes) the player acts only through input (keys, mouse, input actions, UI): getting
  somewhere, collecting, surviving, reaching a state must happen through play. A direct change to the game in an
  episode (`game.set`, or a call that changes state: moving/teleporting an actor, spawning, destroying, setting a
  value) is only for setting up a verification by removing what merely gets in the way (an enemy unrelated to the
  claim), always with why; never the player's position, never what the request is about. In probes, direct changes
  are research (step 2). Every direct change is listed in the report, episodes' and probes' apart.
- Jev decides. Code computes facts and options and executes them; it doesn't play the game by script. A sequence the
  game requires (a recipe) goes into the facts so Jev can follow it. At decision points offer Jev 2+ legal options
  that are each sensible now; a harness whose options always run alone is a script (report refuses it). `report`
  needs an episode in which Jev made at least {min_decisions} decisions among 2+ options: build that into the first
  harness, so the episode that settles the request also counts (a test that takes fewer steps can repeat itself or
  check a neighbouring case).
- Some requests are about how something looks: an animation (a pose, a motion), an effect, a material or light, the
  UI's layout, what is on screen. Read what the game tells first (view's parts, animation and effects, watch, the
  montage, animator state or animation player and its position: see the kit), but only the picture settles them:
  the same numbers can play the wrong animation. Before you look, write down what true would look like and what
  false would (concrete, visible features). Then describe_frames the moment in the episode with open questions
  (what moves and which way, what is at that spot, what the text says) and hold the description against what you
  wrote. Such a request is CONFIRMED or DENIED only on a description (report it with visual: true). A description in
  which the thing isn't seen (off screen, covered, too small, too dark) is not a contradiction: get it in view
  (look_at it, come closer) and play it again. Film a fast motion closely (run_harness frames_fps, up to 20) and
  narrow start and end to it.
- Keep the first run_harness short (20-60 s) unless check_harness showed every option working; then make an episode
  long enough for the plan to reach the goal: a stalled or failed one ends by itself. A one-step request's first
  episode is as long as its step takes to show.
- Errors in the game's log: each run_harness summary lists the errors the game logged during its episode, or says
  none were. That is the log to report from (it can't be read once play has stopped). A summary that says nothing
  about the game's error log means this game's log isn't captured: say so in the report.
- The user watches and may steer while you work: a tool result then says what they typed. It changes the request
  from then on; the game goes on from where it is (the level is never restarted after that, so harness code must
  work from the state play is in).
- The game keeps running during run_harness and probe: enemies attack, timers run. `restart_level` starts over.

{kit}

## Research helpers (in probes: `from autopilot.research import *` is in scope)
{research}
- `check_harness(file="harness.py", seconds=..., restart=True, only=None)`: tries this run's harness without Jev
  (setup, the first observation with its facts, status, plan step and options, then each option's executor alone)
  and reports how each went; `only`: the option names to try.

The contract's docstrings (autopilot/kit/harness.py):
```python
{contract}
```
"""

# A run an earlier session of stopped before it reported (its model calls refused), going on with a new one: what the
# new one is told before the briefing
GOING_ON = """This run is already under way: an earlier session of it stopped before it reported. Go on from where it
was, not from the start: read what it did in {run}/ first (implementer.log: what it read, ran and saw; harnessN.py and
traceN_summary.txt: each episode's harness and what happened), then carry on. The game is as it left it."""

# The words an agent's client says when Aura's relay refused its model call for the run's spending limit (HTTP 402, the
# relay's billing_error): the run goes on on the free allowance, or ends in Aura's words (cli.go_on_aura_free)
SPENT = re.compile(r"\b402\b.*spending limit|billing_error", re.I | re.S)

# How a report the agent didn't write begins (finish): the run's agent stopped before it reported
NO_REPORT = "No report"
GOING_ON_FILE = "going_on.json"  # (in the run's folder: the next session goes on from the earlier one, go_on)

PROMPT = """Request: {prompt}

The game: {project}. This run's folder: {run}/. Write {run}/harness.py, run it, revise, then report.

{briefing}"""

# What the request message carries, so the first turns go to this request rather than to reading what every run reads
BRIEF_FILE = 12000  # characters of notes.md, lib.py or the codex README
BRIEF_RUNS = 8  # earlier runs listed (the latest)
BRIEF_HITS = 24  # codex files listed for the request's words
BRIEF_CHANGES = 30  # codex files listed as changed since the last run
STOP_WORDS = set("""verify that this those these there their they them then than when what where which while with
without from into onto over under after before about does doesn't should would could must will have has been being
player player's players game games level levels make makes made keep keeps goes going still stays same each every
some only also more less most least much many first last other another again time times seconds minutes""".split())


# The game's tools as every agent's model is shown them: (name, description, JSON schema of the input). Their bodies
# are Session's (probe, run_harness, snapshot, describe_frames, restart_level, do_report)
TOOL_SPECS = (
    ("probe",
     "Run Python against the live game (it runs while this tool runs, then pauses). Everything from autopilot.kit "
     "is in scope: `game`, Vec, the controllers... `run(gen, timeout=15)` drives a controller/executor generator to "
     "its end; the research helpers (rows, grep, nearest, teleport, fight_trial, call_any, check_harness...) too. "
     "print() output and the value of the last expression come back. Use it to find actors, read properties, "
     f"describe objects, press inputs and read what changed, and to research by changing the game directly. At most "
     f"{PROBE_SECONDS:.0f} s each: past that the keys are let go, the game paused, and what it printed comes back.",
     {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}),
    ("run_harness",
     f"Run this request's harness.py on the live game for `seconds` of play (5-{MAX_EPISODE:.0f}; default restart: "
     "the level starts over first; restart=false continues play from where it is, and the plan's progress of the "
     "episode before is handed to the harness's resume()) with Jev deciding. Returns a summary: how each option "
     "fared, the timeline, where the plan got, what Jev saw, errors. The full trace is saved beside harness.py as "
     "traceN.jsonl. frames_fps: the frames a second the episode is filmed at for describe_frames "
     f"({vision.FILM_FPS[0]}-{vision.FILM_FPS[1]}; default {media.FPS}: more for a fast animation).",
     {"type": "object", "properties": {"seconds": {"type": "number"}, "restart": {"type": "boolean"},
                                       "frames_fps": {"type": "number"}},
      "required": ["seconds"]}),
    ("snapshot",
     "What the player sees now: where it is, the visible things, what it holds, its body, the texts on screen.",
     {"type": "object", "properties": {}}),
    ("describe_frames",
     f"How the game looked in an episode, in words: up to {vision.MAX_FRAMES} of its frames (spread over start-end) "
     "go with your questions to a vision model that sees only the frames and the questions, never the request. For "
     "what only the picture shows: an animation's motion, an effect, a material or light, the UI's layout. episode: "
     "its number (default: the latest); start, end: game times in it, in seconds (default: all of it; narrow them to "
     f"the moment); questions: 1-{vision.MAX_QUESTIONS} open questions (what moves and how, what is at a spot, what "
     "the text says), never yes/no and never what you hope to see; frames: how many (default "
     f"{vision.DEFAULT_FRAMES}).",
     {"type": "object", "properties": {
         "episode": {"type": "integer"}, "start": {"type": "number"}, "end": {"type": "number"},
         "questions": {"type": "array", "items": {"type": "string"}}, "frames": {"type": "integer"}},
      "required": ["questions"]}),
    ("restart_level",
     "Start the level over (fresh state, the player respawned). level: play this level instead (a path from the "
     "briefing's list of the project's levels): it plays from then on, every restart starting it over.",
     {"type": "object", "properties": {"level": {"type": "string"}}}),
    ("report",
     "The final answer to the request. outcome: for a goal 'achieved' or 'not_achieved'; for a claim 'CONFIRMED', "
     "'DENIED' or 'INCONCLUSIVE'; for an exploration 'explored'. summary: what happened, in a few sentences. "
     "evidence: the observations from play it rests on (with trace file and game times; a description with its "
     "episode and times). findings: for an exploration, each suspected bug with its observation. visual: true when "
     "the outcome rests on how something looks (an animation, an effect, a material or light, the UI). The game's "
     "developer reads it: call the player \"the agent\" (never Jev).",
     {"type": "object", "properties": {
         "outcome": {"type": "string"}, "summary": {"type": "string"},
         "evidence": {"type": "array", "items": {"type": "string"}},
         "findings": {"type": "array", "items": {"type": "string"}}, "visual": {"type": "boolean"}},
      "required": ["outcome", "summary", "evidence"]}),
)
REPORT_FIELDS = ("outcome", "summary", "evidence", "findings", "visual")


def model(provider="anthropic"):
    """The agent's language model for this run: AUTOPILOT_MODEL, else the provider's (MODELS; Anthropic's for
    another)."""
    return (os.environ.get("AUTOPILOT_MODEL") or "").strip() or MODELS.get(provider) or MODELS["anthropic"]


def fast_mode():
    """Whether an Opus model plays in fast mode (the same model, ~2x faster output at twice the price per token):
    on unless AUTOPILOT_FAST=0, read as the environment says it now (autopilot.env is loaded before every run)."""
    return (os.environ.get("AUTOPILOT_FAST") or "").strip() != "0"


def default_timeout():
    """One request's whole budget, in seconds, when the run doesn't give one: AUTOPILOT_TIMEOUT as the environment says
    it now (autopilot.env is loaded before every run, after this module is imported), else TIMEOUT."""
    try:
        seconds = float(os.environ.get("AUTOPILOT_TIMEOUT") or TIMEOUT)
    except ValueError:
        return TIMEOUT
    return seconds if seconds > 0 else TIMEOUT


def research_seconds():
    """A run's seconds of reading and probing before its first episode: AUTOPILOT_RESEARCH_SECONDS as the environment
    says it now (autopilot.env is loaded before every run), else RESEARCH_SECONDS."""
    try:
        seconds = float(os.environ.get("AUTOPILOT_RESEARCH_SECONDS") or RESEARCH_SECONDS)
    except ValueError:
        return RESEARCH_SECONDS
    return seconds if seconds > 0 else RESEARCH_SECONDS


def _contract():
    """The harness contract (autopilot/kit/harness.py) as the agent reads it."""
    with open(HARNESS_DOC, encoding="utf-8") as f:
        src = f.read()
    return src


def kit_doc(engine):
    """The kit's reference for a game of this engine (Engine.kit_doc): KIT.md as it is for Unreal; for the others,
    KIT.md without Unreal's names, then the engine's own section."""
    with open(KIT_DOC, encoding="utf-8") as f:
        kit = f.read()
    return engine.kit_doc(kit)


def research_doc():
    """The research helpers' reference (autopilot/research.py), for the instructions."""
    try:
        from .. import research
    except ImportError:  # (the helpers aren't installed: the instructions don't name them)
        return ""
    lines = research.DOC.strip().splitlines()
    if lines and lines[0].startswith("#"):  # (the instructions give the section its heading)
        lines = lines[1:]
    return "\n".join(lines).strip()


def system_prompt(engine, run_rel, agents=""):
    """The instructions. agents: what this agent's subagents are and when to use them (the Claude Code agent's
    researchers), as a paragraph of step 1; "" for an agent that has none."""
    return (SYSTEM.replace("{engine}", engine.title).replace("{run}", run_rel).replace("{kit}", kit_doc(engine))
            .replace("{research}", research_doc()).replace("{agents}", (" " + agents.strip()) if agents else "")
            .replace("{probe_seconds:.0f}", f"{PROBE_SECONDS:.0f}")
            .replace("{research_minutes:g}", f"{research_seconds() / 60:g}")
            .replace("{contract}", _contract()).replace("{min_decisions}", str(MIN_JEV_DECISIONS)))


class Steering:
    """What the user types while a run plays, to steer it (the plugin's `steer` frames): taken in order by the
    implementer (with its next tool result, or as a message of its own once it has stopped). Thread-safe: the
    frames arrive on another thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items = []

    def add(self, text):
        text = str(text or "").strip()[:2000]
        if text:
            with self._lock:
                self._items.append(text)
        return bool(text)

    def pending(self):
        return bool(self._items)

    def take(self):
        with self._lock:
            items, self._items = self._items, []
        return items


STEER_NOTE = """The user, watching the game, steers: {said}
Take it as a change to the request from now on (what to do next, how, what to stop doing). Revise the harness for it
and go on from the game as it is now: the level is not restarted any more in this run (run_harness continues where
play is; setup() must work from wherever things are now). Then report on the request as steered."""


class Session:
    """One request on one game: the tools' shared state."""

    def __init__(self, game, project, prompt, run_id, say=None, client=None, activity=None):
        self.game = game
        self.project = project
        self.prompt = prompt
        self.root = codexmod.project_dir(project)
        self.run_id = run_id
        self.run_rel = f"runs/{run_id}"
        self.run_dir = os.path.join(self.root, "runs", run_id)
        os.makedirs(self.run_dir, exist_ok=True)
        self._say = say or (lambda text: None)
        self._last_said = None  # (a stage line is said on every tool call: the same one isn't sent twice running)
        self.stage = STAGES[READING]  # the feed's stage now (stage_of the last progress line that named one)
        # The activity feed (docs/protocol.md, `activity`): every event of this run, in order, as sent; the frames go
        # through `activity` (the CLI's link.send) and into runs/<id>/activity.jsonl
        self._activity = activity or (lambda frame: None)
        self.events = []
        self.event_lock = threading.Lock()
        self.calls = {}  # kind -> the id of its latest call event, for the result event's `ends` (CALL_KINDS)
        # In the sandbox's child process the session forwards its events whole to the parent's session, which numbers,
        # logs and sends them (autopilot/sandbox.py): a callable taking the event's fields, or None
        self.forward = None
        self.looking = False  # an episode has been played: what the implementer does now works on its results
        self.client = client
        self.report = None
        self.episodes = 0
        self.played = 0  # the episodes played (their harness loaded)
        self.most_decisions = 0  # the most decisions Jev made in one episode (each among 2+ legal options)
        self.lock = threading.Lock()
        self.probe_lock = threading.Lock()  # one probe at a time: the game is one, and so is the player
        # The last episode's plan progress (runtime's result "progress"): handed to the harness's resume() when the
        # next episode continues play from where it is (run_harness restart=False)
        self.progress = None
        self.writes_after_play = 0  # len(game.writes) when the last episode ended (research changes since: see probe)
        # Whether a player pawn has been seen in this run: a continuation without one is refused only when there was
        # one (dead, a menu); a game played without a pawn at all (a strategy game's camera) continues as it is
        self.pawn_seen = False
        # (opened for each line: a file left open would keep the run's folder from moving on Windows, as a Reset memory
        # moves it once the run is over)
        self.log = os.path.join(self.run_dir, "implementer.log")
        self.log_lock = threading.Lock()
        self.tool_seconds = 0.0
        self.started = time.time()
        self.budget = default_timeout()
        # Reading and probing before the first episode (research_left): their budget, the seconds they used up to the
        # stage now, and when the stage now began; it is reading from when the game was ready (cli.prepare), if known
        self.research_budget = research_seconds()
        self.research_used = 0.0
        ready = getattr(game, "ready_at", None)
        self._stage_since = ready if isinstance(ready, (int, float)) and ready <= self.started else self.started
        self.research_up = False  # (their end is logged once)
        self.sandbox = None  # autopilot.sandbox.Sandbox: model-written code runs there (see _remote)
        self.stop = lambda: False  # true once the run must end (the user pressed Stop, the budget ran out)
        self.steering = Steering()
        self.play_seconds = None  # an exploration's minutes of play, in seconds (None: only the budget bounds play)
        self.play_started = None  # time.time() when the run first played (its minutes of play count from there)
        self.steered = False  # the user has steered this run: the level isn't restarted any more
        self.end_episode = threading.Event()  # (set to end the episode running now: the user steered)
        self.describer = None  # autopilot.vision.Describer: describe_frames (None: not in this run)
        # each episode's video (media.Recorder's begin/end): the describer's recorder, or in the sandbox the parent's
        # (autopilot.sandbox.RemoteRecorder)
        self.recorder = None
        self.described = 0  # the descriptions describe_frames gave
        self.look_failed = None  # why the last describe_frames gave none
        # the user's own agent (autopilot/agents/external.py): it looks at the frames itself (frames), through a
        # subagent of its own, rather than describe_frames' vision model
        self.looks_itself = False
        self.marker = None  # the user's own agent's run: "<run id>-<nonce>", named in what it is told (its transcript)
        self.alerts = []  # what the user must be told with the result (the result's `alerts`)

    def say(self, text):
        """A progress line for the plugin's tab. Once an episode has been played, reading, probing and writing are
        the implementer working on its results (checking what it saw in the game, fixing the harness, writing its
        notes) until it plays again or reports: the tab's Process result stage, never back to Analyze or Build. Before
        that, once the time for reading and probing is up (research_over), they are building the harness: Build."""
        if self.looking and text in (READING, PROBING, WRITING):
            text = RESULTS
        elif text in (READING, PROBING) and self.research_over():
            text = WRITING
        stage = stage_of(text)
        if stage:
            self.to_stage(stage)
        if text == self._last_said:
            return
        self._last_said = text
        self._say(text)

    def to_stage(self, stage):
        """The feed's stage is `stage` from now on; the seconds of the one it ends count to research_used when it was
        reading or probing before the first episode."""
        now = time.time()
        if self.stage in RESEARCH_STAGES and not self.looking:
            self.research_used += now - self._stage_since
        self.stage, self._stage_since = stage, now

    def research_left(self):
        """The seconds of reading and probing this run has left before its first episode (research_budget; below 0
        once they are up), or None once an episode has played (from then on they are the Process result stage)."""
        if self.looking:
            return None
        used = self.research_used
        if self.stage in RESEARCH_STAGES:
            used += time.time() - self._stage_since
        return self.research_budget - used

    def research_over(self):
        """Whether the time for reading and probing is up and no episode has played yet: the run builds the harness."""
        left = self.research_left()
        if left is None or left > 0:
            return False
        if not self.research_up:
            self.research_up = True
            self.note(f"research: the {self.research_budget / 60:g} minutes of reading and probing are up: building "
                      "the harness")
        return True

    def activity(self, kind, text, detail=None, ends=None, stage=None, log=None):
        """One event of the run's activity feed (docs/protocol.md, `activity`): the next id (from 1), `t` seconds
        since the run started, the feed's stage now (or `stage`), kind, text (one line in plain words for the user)
        and detail (what opens under it: code, a path, an output; at most PROBE_RESULT characters). ends: the id of
        the call this is the result of, or its kind (one of CALL_KINDS: the latest call of that kind still open).
        Writes the implementer.log line `log` (default "<kind>: <text>") as note() does, appends the event to
        runs/<id>/activity.jsonl and sends the frame. Returns the event's id."""
        if log is None:
            log = f"{kind}: {text}"
        if self.forward is not None:
            self.forward({"kind": kind, "text": text, "detail": detail, "ends": ends, "stage": stage, "log": log})
            return None
        text = plain_words(scrub(str(text)))
        if detail is not None:
            detail = _cut_lines(plain_words(scrub(str(detail))), PROBE_RESULT)
        with self.event_lock:
            if isinstance(ends, str):
                ends = self.calls.pop(ends, None)
            event = {"id": len(self.events) + 1, "t": round(time.time() - self.started, 1),
                     "stage": stage or self.stage, "kind": kind, "text": text, "detail": detail, "ends": ends}
            if ends is None and kind in CALL_KINDS:
                self.calls[kind] = event["id"]
            self.events.append(event)
            self.note(log)
            with open(os.path.join(self.run_dir, "activity.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        try:
            self._activity({"type": "activity", **event})
        except Exception:
            pass  # (the plugin went: the run goes on, and the event waits in the list for a plugin that asks)
        return event["id"]

    def tool_call(self, name, args, log):
        """An agent's tool call as the feed shows it (autopilot/agents/feed.py: describe_tool), or, for a tool the
        feed has no row for, the log line alone. log: the implementer.log line, as the agent wrote it before."""
        kind, text, detail = feed.describe_tool(name, args, self.root)
        if kind is None:
            self.note(log)
            return None
        if kind == "episode":  # (play begins with the call, before the stage line says so)
            self.to_stage("play")
        return self.activity(kind, text, detail, log=log)

    def said(self, text):
        """What the agent said (a text block): a `text` event whose text is its first line and whose detail is the
        whole when there is more than one."""
        if SPENT.search(str(text)):  # (Aura's relay stopped the agent's model calls: logged, never shown)
            return self.note(f"says: {str(text)[:1500]}")
        lines = [line for line in str(text).splitlines() if line.strip()]
        first = lines[0].strip() if lines else str(text).strip()
        return self.activity("text", first, detail=text if len(lines) > 1 else None, log=f"says: {text[:1500]}")

    def steer_note(self):
        """What the user typed since the last tool result, as the implementer is told it ("" when nothing)."""
        said = self.steering.take()
        if not said:
            return ""
        self.steered = True
        self.activity("steer", "Steered: " + " | ".join(s.strip() for s in said), detail="\n".join(said),
                      log=f"steered: {said}")
        self.say("Revising the play for what you said")
        quoted = "\n".join(f'"{s}"' for s in said)
        return "\n\n" + STEER_NOTE.format(said=quoted)

    def _remote(self, method, **kwargs):
        """Session.<method> in the sandbox's process; its counters copied back."""
        try:
            value = self.sandbox.call(method, timeout=MAX_EPISODE + 180, **kwargs)
            state = self.sandbox.call("state", timeout=30)
        except Exception as e:
            return f"The sandbox failed: {e}"
        self.episodes = state.get("episodes", self.episodes)
        self.played = state.get("played", self.played)
        self.most_decisions = state.get("most_decisions", self.most_decisions)
        self.sandbox_writes = state.get("writes") or []
        return value

    def play_left(self):
        """An exploration's seconds of play left (None when the run has no such limit). They count from the first
        episode: reading the game and writing the harness before it don't use them."""
        if self.play_seconds is None:
            return None
        return self.play_seconds - (time.time() - self.play_started if self.play_started else 0.0)

    def out_of_time(self):
        """Whether an exploration's play time is up and its agent has had PLAY_GRACE to report since: the run ends."""
        left = self.play_left()
        return left is not None and left < -PLAY_GRACE

    def clock(self):
        """The time used, for every tool result: the implementer can't see a clock otherwise."""
        used = time.time() - self.started
        share = used / self.budget if self.budget else 0.0
        line = f"[time: {used / 60:.1f} of {self.budget / 60:.0f} min used]"
        left = self.play_left()
        if left is not None:
            line = (f"[time: {used / 60:.1f} min used; play: {max(0.0, self.play_seconds - left) / 60:.1f} of "
                    f"{self.play_seconds / 60:.0f} min used]")
        research = self.research_left()
        if research is not None:  # (before the first episode)
            line = line[:-1] + (f"; reading and probing: {(self.research_budget - max(0.0, research)) / 60:.1f} of "
                                f"{self.research_budget / 60:g} min used]")
            if self.report is None and self.research_over():
                return line + (f" The time for reading and probing is up: write {self.run_rel}/harness.py now from "
                               "what you know and call run_harness. Probes are closed until an episode has played.")
            if self.report is None and research < 0.2 * self.research_budget:
                line += (f" {research:.0f} s of reading and probing left: settle what the harness needs, then write "
                         "it.")
        if left is not None:
            if self.report is None and left < 5.0:
                line += (" The exploration's play time is up: call report now with what the episodes so far showed "
                         f"(the run ends in {max(0.0, PLAY_GRACE + left):.0f} s).")
            elif self.report is None and left < 0.3 * self.play_seconds:
                line += f" {left:.0f} s of play left: make the next episode the last, then report."
            return line
        if self.report is None and share >= 0.9:
            line += (" Time is nearly up: call report now with what the episodes so far show (INCONCLUSIVE if the "
                     "request isn't settled).")
        elif self.report is None and share >= 0.7:
            line += " Wrap up: once an episode settles the request, report."
        return line

    def note(self, text):
        with self.log_lock, open(self.log, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")

    # -- the tools' bodies (synchronous; run in a thread) ------------------------------------------------

    def _namespace(self, out):
        import autopilot.kit as kit

        ns = {k: getattr(kit, k) for k in dir(kit) if not k.startswith("_")}
        ns.update(engines.of(self.game).constants)  # (the engine's names, e.g. Unreal's GAMEPLAY)
        ns.update(game=self.game, kit=kit, control=control, json=json, time=time)

        def _print(*a, **k):
            k.pop("file", None)
            print(*a, file=out, **k)

        def run(gen, timeout=15.0, hz=15.0):
            """Drive a controller/executor generator until it returns (or timeout s); returns its result (the kit's
            run(), on this game: harness code has the same)."""
            return control.run(gen, timeout=timeout, hz=hz, game=self.game)

        ns.update(print=_print, run=run)
        try:
            from .. import research

            ns.update(research.names_for(self.game))  # (the codex helpers bound to this game's codex folder)
        except ImportError:
            pass

        def check_harness(file="harness.py", **kwargs):
            """runtime.check on this run's harness (as the kit's reference says): the harness as it is on disk now."""
            path = os.path.join(self.run_dir, file)
            if not os.path.exists(path):
                return f"{self.run_rel}/{file} doesn't exist yet: write it first."
            sys.modules.pop("lib", None)
            return runtime.check(self.game, runtime.load_harness(path), **kwargs)

        ns.update(check_harness=check_harness)
        if self.root not in sys.path:
            sys.path.insert(0, self.root)
        return ns

    def probe(self, code, keep_running=False, seconds=None):
        """The probe tool's body. seconds: the longest it may run; by default PROBE_SECONDS, or what is left of the
        time for reading and probing when that is less (the sandbox's child is passed the parent's). Refused once that
        time is up, until an episode has played."""
        if seconds is None:
            if self.research_over():
                text = (f"Not run: the {self.research_budget / 60:g} minutes of reading and probing before the first "
                        f"episode are up. Write {self.run_rel}/harness.py from what you know now and call run_harness; "
                        "probes open again once an episode has played.")
                self.activity("probe", "Not run: the time for trying things before the harness is up", detail=text,
                              ends="probe", log="probe: refused, the time for reading and probing is up")
                return text
            left = self.research_left()
            seconds = PROBE_SECONDS if left is None else max(1.0, min(PROBE_SECONDS, left))
        if self.sandbox:
            return self._remote("probe", code=code, keep_running=keep_running, seconds=seconds)
        with self.probe_lock:
            return self._probe(code, keep_running, seconds)

    def _probe(self, code, keep_running=False, seconds=PROBE_SECONDS):
        out = io.StringIO()
        ns = self._namespace(out)
        value = [None]
        t0 = time.time()
        written = len(self.game.writes)
        done = threading.Event()

        def work():
            try:
                tree = ast.parse(code)
                last = tree.body[-1] if tree.body and isinstance(tree.body[-1], ast.Expr) else None
                if last is not None:
                    tree.body = tree.body[:-1]
                exec(compile(tree, "<probe>", "exec"), ns)
                if last is not None:
                    value[0] = eval(compile(ast.Expression(last.value), "<probe>", "eval"), ns)
            except Exception:
                out.write(traceback.format_exc(limit=5))
            finally:
                done.set()

        late = ""
        try:
            self.game.pause(False)
            # The code runs in a thread of its own so a probe that runs long (a controller that never returns, a
            # read of everything) ends for the agent at `seconds`: its thread goes on to its end, but with the keys
            # let go and the game paused it can't drive the player any more
            threading.Thread(target=work, name="probe", daemon=True).start()
            if not done.wait(seconds):
                late = ("\nTHE TIME FOR READING AND PROBING RAN OUT DURING THE PROBE AND IT WAS CUT OFF HERE: the keys "
                        "were let go and the game paused; what it printed until then is above. Write the harness now "
                        "from what you know and call run_harness." if seconds < PROBE_SECONDS else
                        f"\nTHE PROBE TOOK MORE THAN {PROBE_SECONDS:.0f} s AND WAS CUT OFF HERE: the keys were let go "
                        "and the game paused; what it printed until then is above. Keep a probe to one question's "
                        "worth; what takes long (whole walks, a fight to the end) belongs in run_harness.")
        finally:
            try:
                self.game.release_all()
                if not keep_running:
                    self.game.pause(True)
            except Exception:
                pass
        for w in self.game.writes[written:]:  # (research changes: told apart from an episode's in the report)
            if isinstance(w, dict):
                w["probe"] = True
        text = out.getvalue()
        if value[0] is not None:
            text += ("" if text.endswith("\n") or not text else "\n") + _show(value[0])
        text += late
        dt = time.time() - t0
        lines = len(text.splitlines())
        what = (f"Cut off after {seconds:.0f} s" if late else
                f"Failed after {dt:.1f} s" if "Traceback (most recent call last)" in text else
                f"Got {lines} line{'s' if lines != 1 else ''} back in {dt:.1f} s")
        self.activity("probe", what, detail=text or "(no output)", ends="probe",
                      log=f"probe ({dt:.1f}s):\n{code}\n-> {text[:2000]}")
        return _cut(text or "(no output)", 12000)

    def run_harness(self, seconds, restart=True, file="harness.py", until=None, frames_fps=None):
        if frames_fps and self.describer is not None:
            # (the recording is the run's, here: the episode, in the sandbox or not, is filmed closer)
            with self.describer.filming(frames_fps):
                return self.run_harness(seconds, restart, file, until)
        kept = ""
        if restart and (self.steered or self.steering.pending()):
            # (the user is steering live play: it goes on from where it is, never from a restarted level or game)
            restart = False
            kept = "(Not restarted: the user is steering live play, so the episode went on from where the game was.)\n"
        played = self.played
        t0 = time.time()
        left = self.play_left()
        if left is not None:  # (an exploration: its episodes end when its minutes of play are up)
            if left < 5.0:
                return (f"No episode: the exploration's {self.play_seconds / 60:.0f} minutes of play are used up. Call "
                        "report now with what the episodes so far showed.")
            seconds = min(float(seconds), left)
        until = t0 + left if left is not None else until  # (the sandbox's own session is passed it)
        if self.sandbox:
            text = self._remote("run_harness", seconds=seconds, restart=restart, file=file, until=until)
        else:
            text = self._run_harness(seconds, restart, file, until)
        if self.played > played:
            self.looking = True  # (_run_harness said RESULTS, where it played)
            if self.play_started is None and self.play_seconds is not None:
                self.play_started = t0
        return kept + text

    def _episode_over(self, until=None):
        """Whether the episode running now must end: the run stops, the user steered, or (until: a time.time()) an
        exploration's play time is up."""
        return (bool(self.stop()) or self.end_episode.is_set() or self.steering.pending()
                or (until is not None and time.time() >= until))

    def _run_harness(self, seconds, restart, file, until=None):
        self.end_episode.clear()
        path = os.path.join(self.run_dir, file)
        if not os.path.exists(path):
            return self._no_episode(f"{self.run_rel}/{file} doesn't exist yet: write it first.")
        seconds = max(5.0, min(float(seconds), MAX_EPISODE))
        self.episodes += 1
        n = self.episodes
        trace_path = os.path.join(self.run_dir, f"trace{n}.jsonl")
        try:
            sys.modules.pop("lib", None)  # the project's lib.py as it is now
            if self.root not in sys.path:
                sys.path.insert(0, self.root)
            harness = runtime.load_harness(path)
        except Exception:
            return self._no_episode("The harness doesn't load:\n" + traceback.format_exc(limit=6))
        self.played += 1
        self.say(f"{PLAYING} (run {n})")
        wall0 = time.time()
        try:
            note = ""
            resume = None
            if restart:
                if self.game.restart(map=getattr(harness, "start_map", None), wait_for_pawn=10.0):
                    self.pawn_seen = True
                else:
                    note = ("Note: after the restart there was no player pawn within 10 s (a main menu? a loading "
                            "screen?): setup() ran anyway. Set start_map to the gameplay level, or get through the "
                            "menu in setup().\n")
                self.game.pause(False)
            else:
                # Play goes on from where it is: the game stays paused through setup() and the first observation,
                # and runs again just before the player is first driven (unpaused, an enemy hits a player that
                # nobody drives yet). A player that was there and is gone (dead, a menu) leaves nothing to go on
                # from; a game that never had one (a strategy game's camera) goes on as it is
                try:
                    alive = bool(self.game.context(fresh=True).get("character"))
                except Exception:
                    alive = True
                if alive:
                    self.pawn_seen = True
                if not alive and self.pawn_seen:
                    self.episodes -= 1
                    self.played -= 1
                    return self._no_episode(
                        "Not played: there is no player pawn to continue with (dead with no respawn? a menu or "
                        "loading screen?). Find out what the screen shows (a probe: game.screen(), game.ui()), get "
                        "through a menu in a probe, or run_harness with a restart (the level starts over; "
                        "setup() must get through any menu).")
                self.game.pause(True)
                resume = lambda: self.game.pause(False)
            # Play that goes on from where it is continues the plan too: the last episode's progress reaches the
            # harness's resume(); a restarted level starts the plan over
            progress = self.progress if not restart else None
            if not restart:
                # A research change made since the last episode bears on this play (the player teleported out of
                # the spot it was stuck in): it counts as a change to the play, not to research
                for w in self.game.writes[self.writes_after_play:]:
                    if isinstance(w, dict) and w.get("probe"):
                        w["in_play"] = True
            rt = runtime.Runtime(self.game, harness, trace_path, client=self.client, max_seconds=seconds,
                                 stop=lambda: self._episode_over(until), say=self.say, resume=resume,
                                 telemetry=self.telemetry(), progress=progress)
            # The episode's own video (media.Recorder): from here to its end, which it ends on (before the game pauses)
            recorder = self.recorder or getattr(self.describer, "recorder", None)
            if recorder is not None:
                recorder.begin(n)
            try:
                result = rt.run()
            finally:
                if recorder is not None:
                    recorder.end(n)
            if result.get("reason") == "stopped" and until is not None and time.time() >= until:
                note += "Note: the exploration's minutes of play are up: the episode ended there.\n"
            elif result.get("reason") == "stopped" and not self.stop():
                note += ("Note: the user steered while Jev played: the episode ended there, and the game is paused "
                         "where it was.\n")
        except Exception:
            return self._no_episode("The runtime failed:\n" + traceback.format_exc(limit=8))
        finally:
            try:
                self.game.release_all()
                self.game.pause(True)
            except Exception:
                pass
            self.say(RESULTS)
        summary = runtime.summarize(trace_path, error_log=engines.of(self.game).error_log)
        with open(os.path.join(self.run_dir, f"trace{n}_summary.txt"), "w", encoding="utf-8") as f:
            f.write(summary)
        # keep the version that ran beside its trace
        with open(path, encoding="utf-8") as src, open(os.path.join(self.run_dir, f"harness{n}.py"), "w",
                                                        encoding="utf-8") as dst:
            dst.write(src.read())
        decisions = int(result.get("decisions") or 0)
        self.most_decisions = max(self.most_decisions, decisions)
        if isinstance(result.get("progress"), dict):
            self.progress = dict(result["progress"], episode=n)
        self.writes_after_play = len(self.game.writes)
        try:
            if result.get("reason") == "the player is gone" or self.game.context(fresh=True).get("character"):
                self.pawn_seen = True
        except Exception:
            pass
        played_for = (result.get("progress") or {}).get("t")
        if played_for is None:
            played_for = time.time() - wall0
        self.activity("episode", f"Episode {n}: {played_for:.0f} s of play ({result.get('reason') or 'ended'})",
                      detail=summary, ends="episode",
                      log=f"run_harness {n} ({seconds:.0f}s): {result.get('reason')}, {result.get('decisions')} Jev "
                          "decisions" + (" (continued from episode %s)" % progress.get("episode") if progress else ""))
        if self.most_decisions < MIN_JEV_DECISIONS:
            note += (f"Note: Jev made {decisions} decisions in this episode; report needs an episode with at least "
                     f"{MIN_JEV_DECISIONS} (decisions among 2+ options), so this one can't settle the request.\n")
        return (note + f"Episode {n} ({self.run_rel}/trace{n}.jsonl; harness saved as harness{n}.py)\n"
                + _cut(summary, 14000))

    def _no_episode(self, text):
        """run_harness played nothing (text says why): the feed's episode row ends on it; the agent reads text."""
        self.activity("episode", "No episode: " + feed.first_line(text), detail=text, ends="episode",
                      log=f"run_harness: {feed.first_line(text)}")
        return text

    def telemetry(self):
        """The run's telemetry folder when its data is shared (the run made it: autopilot/telemetry.py), else None."""
        folder = os.path.join(self.run_dir, "telemetry")
        return folder if os.path.isdir(folder) else None

    def still(self, reason, **extra):
        """A still of the game now, kept with the run's data when it is shared (autopilot/media.py)."""
        folder = self.telemetry()
        if folder:
            from .. import media

            media.still_later(self.game.link, folder, reason, extra)

    def snapshot(self):
        if self.sandbox:
            return self._remote("snapshot")
        self.still("snapshot")
        g = self.game
        out = {}
        try:
            g.pause(False)
            p = g.pose()
            out["player"] = {"pawn": g.pawn, "at": p["at"].rounded(), "yaw": round(p["yaw"]),
                             "pitch": round(p["pitch"]), "speed": round(p["velocity"].length)}
            v = g.view(max_count=16, max_distance=4000)
            out["visible"] = [{k: t.get(k) for k in ("name", "class", "distance", "screen_x", "screen_y", "label")
                               if t.get(k) is not None} for t in (v or {}).get("visible", [])]
            out["held"] = [h.get("name") for h in (v or {}).get("held", [])]
            out["body"] = {k: (v or {}).get("body", {}).get(k) for k in ("on_ground", "falling", "crouched")}
            out["screen"] = g.screen()
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        finally:
            try:
                g.pause(True)
            except Exception:
                pass
        return _cut(json.dumps(out, indent=1, default=str), 10000)

    def opening(self):
        """What the live game shows as the run starts, for the request message: the level, the player (or none: a
        menu?), the controls, the texts and clickable elements on screen, what the player sees. Every run's first
        probe looked for this."""
        if self.sandbox:
            return self._remote("opening")
        g = self.game
        out = {}

        def part(key, fn):
            try:
                out[key] = fn()
            except Exception as e:
                out[key] = f"(couldn't read: {type(e).__name__}: {e})"

        net = {}
        try:
            g.pause(False)
            c = g.context(fresh=True)
            net = c.get("net") if isinstance(c.get("net"), dict) else {}
            out["level"] = c.get("world")
            out["player"] = c.get("character") or "(no player pawn: a menu, a loading screen, dead?)"
            out["game_time"] = round(float(c.get("game_time") or 0.0), 1)
            out["controls"] = {x.get("name"): x.get("keys") for x in c.get("controls") or []}
            # (the plugin's notes on how input reaches the game, e.g. Unity's: point before clicking, or keys can't)
            notes = c["input"].get("notes") if isinstance(c.get("input"), dict) else None
            if isinstance(notes, list) and notes:
                out["input_notes"] = notes
            if c.get("character"):
                def player():
                    p = g.pose()
                    return {"at": p["at"].rounded(), "yaw": round(p["yaw"]), "pitch": round(p["pitch"]),
                            "speed": round(p["velocity"].length)}

                def seen():
                    v = g.view(max_count=16, max_distance=4000) or {}
                    return {"visible": [{k: t.get(k) for k in ("name", "class", "distance", "label")
                                         if t.get(k) is not None} for t in v.get("visible", [])],
                            "held": [h.get("name") for h in v.get("held", [])],
                            "body": {k: v.get("body", {}).get(k) for k in ("on_ground", "falling", "crouched")}}
                part("pose", player)
                part("sees", seen)
            part("screen_texts", lambda: dict(list(g.screen().items())[:60]))
            part("ui", lambda: [{k: e.get(k) for k in ("id", "kind", "label", "enabled") if e.get(k) not in (None, "")}
                                for e in g.ui()[:40]])
        except Exception as e:
            out["error"] = f"{type(e).__name__}: {e}"
        finally:
            paused = None
            try:
                paused = g.pause(True)
            except Exception:
                pass
        if net.get("mode") in ("client", "listen_server"):
            out["network"] = network_note(net, paused)
        out = runtime.jsonable(out)
        return _cut("\n".join(f"{k}: {json.dumps(v, default=str)}" for k, v in out.items()), 10000)

    def alert(self, text):
        """Tell the user text with the result (once)."""
        if text not in self.alerts:
            self.alerts.append(text)
            self.activity("alert", text, log=f"alert: {text}")

    def looking_note(self):
        """The request message's line on describe_frames: what it can look at in this run, or why it can't."""
        if self.looks_itself:
            why = self.describer.cannot_film() if self.describer is not None else "the game isn't filmed in this run"
            if why:
                return (f"## Looking at the picture\nThe game's frames can't be had in this run: {why}. A request "
                        "about how something looks can't be settled: report it INCONCLUSIVE, with visual: true, saying "
                        "so.")
            return (f"## Looking at the picture\nThe game is filmed while it plays ({media.FPS} frames a second; "
                    f"run_harness frames_fps films an episode at up to {vision.FILM_FPS[1]}): a describer subagent "
                    "looks at an episode's frames (INSTRUCTIONS.md, \"Looking at the picture here\").")
        why = self.describer.cannot() if self.describer is not None else "it isn't set up in this run"
        if why:
            return (f"## Looking at the picture\ndescribe_frames can't be used in this run: {why}. A request about how "
                    "something looks can't be settled: report it INCONCLUSIVE, with visual: true, saying so.")
        return (f"## Looking at the picture\nThe game is filmed while it plays ({media.FPS} frames a second; "
                f"run_harness frames_fps films an episode at up to {vision.FILM_FPS[1]}): describe_frames describes "
                "an episode's frames.")

    def describe_frames(self, questions, episode=None, start=None, end=None, frames=None):
        """describe_frames: an episode's frames (between game times start and end), described with questions."""
        if self.describer is None:
            return "describe_frames isn't available in this run."
        if self.looks_itself and self.describer.route is None:
            # (the user's own agent: its describer subagent looks; this fallback has no model, which is no fault)
            return ("describe_frames, Autopilot's own describer, isn't available here (no key for one). Have your "
                    "describer subagent look instead: it calls `frames` for the episode's pictures (INSTRUCTIONS.md, "
                    "\"Looking at the picture here\").")
        why = self.describer.unavailable()
        if why:
            self.look_failed = why
            self.alert(self.describer.alert(why))
            return (f"Can't look at the game's picture in this run: {why}. A request about how something looks is "
                    "INCONCLUSIVE here (report visual: true); the user is told why.")
        if isinstance(questions, str):
            questions = [questions]
        n, span = self._episode_span(episode)
        if span is None:
            return n
        wall1 = span.get("wall_end") or time.time()
        label = f"episode {n}"
        self.say(RESULTS)
        text, ok = self.describer.describe(span["wall_start"] - 0.5, wall1 + 0.5, questions, frames or
                                           vision.DEFAULT_FRAMES, t0=start, t1=end, label=label)
        if ok:
            self.described += 1
            self.look_failed = None
        else:
            self.look_failed = text
            if self.describer.no_picture:  # (the game drew no picture while the episode played)
                self.alert(self.describer.alert("no picture came from the game while it played (its window was "
                                                "minimized or hidden)"))
        self.activity("look", f"Looked at {label}'s frames" if ok else f"Couldn't look at {label}'s frames",
                      detail=text, ends="look", log=f"describe_frames ({label}): {questions} -> {text[:3000]}")
        return text

    def _episode_span(self, episode=None):
        """(its number, the span episode_spans() has of it) for an episode (default: the latest), or (why there is
        none, None)."""
        n = int(episode or self.episodes or 0)
        span = next((s for s in episode_spans(self.run_dir) if s["episode"] == f"trace{n}"), None)
        if span is None or span.get("wall_start") is None:
            return (f"No episode {n} has been played in this run." if n else
                    "No episode has been played yet: run_harness first (its frames are what can be looked at)."), None
        return n, span

    def frames(self, episode=None, start=None, end=None, count=None):
        """frames, for the user's own agent (autopilot/agents/external.py), whose describer subagent looks at them
        itself: (text, [JPEG bytes]) of up to vision.MAX_FRAMES of an episode's frames (between game times start and
        end), or (why there are none, []). Frames had count as a description for report's visual rule."""
        if self.describer is None:
            return "The game isn't filmed in this run.", []
        why = self.describer.unfilmed()
        if why:
            self.look_failed = why
            self.alert(self.describer.alert(why, filming=True))
            return (f"Can't look at the game's picture in this run: {why}. A request about how something looks is "
                    "INCONCLUSIVE here (report visual: true); the user is told why."), []
        n, span = self._episode_span(episode)
        if span is None:
            return n, []
        label = f"episode {n}"
        self.say(RESULTS)
        chosen, frames, why = self.describer.frames_of(span["wall_start"] - 0.5, (span.get("wall_end") or time.time())
                                                       + 0.5, count or vision.DEFAULT_FRAMES, start, end, label)
        if why:
            self.look_failed = why
            if self.describer.no_picture:
                self.alert(self.describer.alert("no picture came from the game while it played (its window was "
                                                "minimized or hidden)", filming=True))
            self.activity("look", f"No frames of {label}", detail=why, ends="look", log=f"frames ({label}): {why}")
            return why, []
        self.described += 1
        self.look_failed = None
        times = ", ".join(f"{f['t']:.2f}" if f["t"] is not None else "?" for f in chosen)
        self.activity("look", f"Took {len(chosen)} frames of {label}", detail=f"At game times {times} s",
                      ends="look", log=f"frames ({label}): {len(chosen)} of {len(frames)}, at game times {times}")
        return (f"{len(chosen)} frames of {len(frames)} ({label}), in order, at game times {times} s "
                f"(run {self.marker or self.run_id}):"), [f["jpeg"] for f in chosen]

    def restart_level(self, level=None):
        if self.steered or self.steering.pending():
            return ("Not restarted: the user is steering live play. Go on from where the game is (run_harness "
                    "continues it).")
        info = getattr(self.game, "level_info", None)
        target = levelsmod.resolve(level, info.levels if info else None) if level else None
        if level and not target:
            return (f"Not restarted: the project has no level {level}. Give one of the briefing's list (its path), "
                    f"or no level to start this one over (still playing {self.game.level or 'the level it was'}).")
        if self.sandbox:
            reply = self._remote("restart_level", **({"level": target} if target else {}))
        else:
            try:
                ok = self.game.restart(map=target)
                self.game.pause(True)
            except Exception as e:
                return f"Restart failed: {e}" + (f" (still playing {self.game.level or 'the level it was'})"
                                                  if target else "")
            where = f" in {target}" if target else ""
            reply = f"Restarted{where}." if ok else f"Restarted{where}, but no player appeared within 20 s."
        if target and str(reply).startswith("Restarted"):
            self.game.level = target  # (every later restart starts it over)
            if info is not None:
                info.playing = target
            self.activity("restart", f"Restarted the level {levelsmod.short(target)}", detail=target,
                          log=f"level: {target}")
        elif str(reply).startswith("Restarted"):
            self.activity("restart", "Restarted the level", log="level: restarted")
        return reply

    def do_report(self, args):
        refused = None
        if self.episodes == 0 and str(args.get("outcome", "")).upper() != "INCONCLUSIVE":
            # Probes check assumptions; play is Jev's, in the harness. A verdict or goal resting on probes alone
            # would be a script playing the game, not Jev
            refused = ("Not reported: no run_harness episode has been played yet. Write harness.py and run it: the "
                       "outcome must rest on an episode in which Jev played (probes only check assumptions). Report "
                       "INCONCLUSIVE if the game can't be played at all.")
        elif self.most_decisions < MIN_JEV_DECISIONS and str(args.get("outcome", "")).upper() != "INCONCLUSIVE":
            refused = (f"Not reported: in no episode did Jev make {MIN_JEV_DECISIONS} decisions (the most: "
                       f"{self.most_decisions}). Options that run alone are a script, not Jev playing. Give Jev real "
                       "choices: at its decision points offer 2+ legal options that are each a sensible thing to do "
                       "now (e.g. which target, approach or action next, or keep going vs. something else), then run "
                       "again.")
        elif str(args.get("visual")).strip().lower() == "true" and not self.described:
            # A claim about how something looks rests on a description of its frames, by a model not told the claim:
            # the agent's own reading of numbers (or hopes) isn't what the picture showed
            why = ("describe_frames isn't set up" if self.describer is None else
                   self.describer.unfilmed() if self.looks_itself else self.describer.unavailable())
            if why:
                if self.describer is not None:
                    self.alert(self.describer.alert(why, filming=self.looks_itself))
                if str(args.get("outcome", "")).upper() in ("CONFIRMED", "DENIED"):
                    refused = (f"Not reported: the outcome rests on how something looks, and the game's picture can't "
                               f"be looked at in this run ({why}). Report INCONCLUSIVE, saying so.")
            elif str(args.get("outcome", "")).upper() in ("CONFIRMED", "DENIED"):
                refused = ("Not reported: the outcome rests on how something looks, and no "
                           + ("description of the episode's frames (frames, looked at by a describer subagent)"
                              if self.looks_itself else "describe_frames description")
                           + " backs it" + (f" (the last one failed: {self.look_failed})" if self.look_failed else "")
                           + ". Describe the episode's moment (open questions), hold the description against what "
                           "true and false would look like, then report.")
        if refused:
            self.note(refused)  # (the log shows why the run went on after a report)
            return refused
        self.report = args
        self.to_stage("report")
        outcome = str(args.get("outcome") or "").strip()
        evidence = args.get("evidence") if isinstance(args.get("evidence"), list) else []
        self.activity("report", f"Reported {outcome}" if outcome else "Reported",
                      detail=str(args.get("summary") or "") + "".join(f"\n- {e}" for e in evidence),
                      log=f"reported: {outcome}")
        self.still("report", outcome=args.get("outcome"))
        with open(os.path.join(self.run_dir, "report.json"), "w", encoding="utf-8") as f:
            json.dump(args, f, indent=1)
        return "Reported. You can stop now."


def network_note(net, paused=None):
    """The opening's line on a multiplayer game (the plugin's context `net`): whose player the run plays, where the
    truth is kept, and whether the game pauses. A single-player game has none."""
    if net.get("mode") == "listen_server":
        return ("Multiplayer: the game is hosted by the player you play (a listen server), so this world has "
                "authority: what you read is the game's truth and a change from a probe sticks.")
    server = net.get("server_world") or ""
    clients = int(net.get("clients") or 1)
    note = ["Multiplayer: you play a client of a dedicated server" + (
        f" (the first of {clients} clients in this editor)" if clients > 1 else "") + ". game.world, game.pawn, "
        "game.controller, input, the view and the screen are the client's: what this player sees and controls."]
    if server:
        note.append(f"The server's world is {server}: the game's truth is there (health, damage, deaths, AI, the "
                    "rules), and the client's copies of actors show only what the server sent them. A change from a "
                    "probe (set, call, a teleport) sticks only when made on the server's copy: the player's is "
                    f"{net.get('server_character') or '(none yet)'} "
                    f"(controller {net.get('server_controller') or '-'}); "
                    f"find other server actors with game.call(GAMEPLAY, \"GetAllActorsOfClass\", "
                    f"WorldContextObject=\"{server}\", ActorClass=...).")
    else:
        note.append("The server runs in another process: its state can't be read from here, only what it sent the "
                    "client.")
    if isinstance(paused, dict) and paused.get("why"):
        note.append(f"The game can't be paused ({paused['why']}): it runs on while you read or think.")
    note.append("Play the game as the editor is set up to play it: don't change the editor's Play settings (the net "
                "mode is the user's).")
    return " ".join(note)


def _read(path, n=BRIEF_FILE):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read().strip()
    except OSError:
        return None
    return text if len(text) <= n else text[:n] + f"\n... ({len(text) - n} more characters: Read the file for them)"


def _stem(word):
    """The word without an ending, so a search finds its other forms (painting, painted -> paint; boxes -> box)."""
    if word.endswith("es") and word[:-2].endswith(("s", "x", "z", "ch", "sh")) and len(word) >= 5:
        return word[:-2]
    for suffix in ("ing", "ed", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4 and not word.endswith("ss"):
            return word[:-len(suffix)]
    return word


def word_hits(codex_root, prompt, limit=BRIEF_HITS):
    """Where the request's words appear in the codex: its files (not the top-level lists) ranked by how many of the
    words they have, rarer words counting more and a word in the file's name most. Lines, or None."""
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z']+", prompt):
        w = w.lower().removesuffix("'s").strip("'")
        if len(w) >= 4 and w not in STOP_WORDS and _stem(w) not in words:
            words.append(_stem(w))
    if not words:
        return None
    files = []
    for top, dirs, names in os.walk(codex_root):
        if os.path.samefile(top, codex_root):
            continue  # (assets.txt, maps.txt, level.txt...: lists that name everything)
        for name in names:
            path = os.path.join(top, name)
            try:
                if os.path.getsize(path) > 2_000_000:
                    continue
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read().lower()
            except OSError:
                continue
            rel = os.path.relpath(path, codex_root).replace(os.sep, "/")
            files.append((rel, {w: text.count(w) for w in words}, rel.lower()))
    if not files:
        return None
    common = [w for w in words if len(files) >= 20 and sum(1 for _, n, _ in files if n[w]) > 0.3 * len(files)]
    rare = [w for w in words if w not in common]
    weight = {w: math.log(len(files) / (1 + sum(1 for _, n, _ in files if n[w]))) + 1.0 for w in rare}
    scored = []
    for rel, n, low in files:
        score = sum(weight[w] * (1.5 if w in os.path.basename(low) else 1.0) for w in rare if n[w])
        if score > 0:
            scored.append((sum(1 for w in rare if n[w]), score, sum(n[w] for w in rare), rel, n))
    scored.sort(key=lambda s: (-s[0], -s[1], -s[2], s[3]))
    lines = [f"- codex/{rel} ({', '.join(f'{w}: {n[w]}' for w in rare if n[w])})" for *_, rel, n in scored[:limit]]
    if common:
        lines.append(f"(in most files, so not ranked: {', '.join(common)})")
    if len(scored) > limit:
        lines.append(f"({len(scored) - limit} more files have some of them: grep for the rest)")
    missing = [w for w in rare if not any(n[w] for _, n, _ in files)]
    if missing:
        lines.append(f"(not in the codex: {', '.join(missing)})")
    return lines


def briefing(session, opening=None):
    """The request message's second half: what every run used to read in its first turns."""
    root = session.root
    out = []
    level = levelsmod.briefing(getattr(session.game, "level_info", None))
    if level:  # (first: which level to play is the first decision)
        out.append(level)
    notes, lib = _read(os.path.join(root, "notes.md")), _read(os.path.join(root, "lib.py"))
    if notes:
        out.append(f"## notes.md (what earlier requests on this game verified)\n{notes}")
    if lib:
        out.append(f"## lib.py (importable as `import lib`)\n```python\n{lib}\n```")
    if not notes and not lib:
        out.append("## notes.md, lib.py\nNone yet: the first request on this game.")
    runs = []
    runs_dir = os.path.join(root, "runs")
    for rid in sorted(os.listdir(runs_dir)) if os.path.isdir(runs_dir) else []:
        if rid == session.run_id:
            continue
        prompt = _read(os.path.join(runs_dir, rid, "prompt.txt"), 300)
        try:
            with open(os.path.join(runs_dir, rid, "report.json"), encoding="utf-8") as f:
                rep = json.load(f)
        except (OSError, ValueError):
            rep = {}
        if prompt:
            runs.append(f"- runs/{rid}: {prompt} -> {rep.get('outcome', 'no report')}: "
                        f"{str(rep.get('summary', ''))[:300]}")
    if runs:
        out.append("## Earlier runs (each folder keeps its harness versions, traces and report)\n"
                   + "\n".join(runs[-BRIEF_RUNS:]))
    codex_root = os.path.join(root, "codex")
    try:
        since = codex_since(session, codex_root)
    except Exception as e:  # (it helps, but a run never fails for it)
        session.note(f"codex changes: {e}")
        since = None
    if since and (notes or lib or runs):  # (with nothing learned yet, nothing can be out of date)
        out.append(since)
    readme = _read(os.path.join(codex_root, "README.md"))
    if readme:
        out.append(f"## codex/README.md\n{readme}")
    try:
        hits = word_hits(codex_root, session.prompt) if os.path.isdir(codex_root) else None
    except Exception:
        hits = None
    if hits:
        out.append("## Where the request's words appear in the codex (files, with how often)\n" + "\n".join(hits))
    if opening:
        out.append(f"## The live game now (looked at just before you started; it is paused)\n{opening}")
    return "\n\n".join(out)


NAMED_IN = ("notes.md", "lib.py")  # what runs wrote down, which may name codex files


def _written(root):
    """{"notes.md": its text, "lib.py": its text}, whole ("" when there is none)."""
    out = {}
    for name in NAMED_IN:
        try:
            with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                out[name] = f.read()
        except OSError:
            out[name] = ""
    return out


def passages(path, texts):
    """{name: its passages that name the codex file at path} for each of texts ({name: text}) that names it: notes.md
    by line, lib.py by top-level function or class (by line when it doesn't parse). A file is named by its name
    without the extension (BP_Door for blueprints/BP_Door.txt), as a whole word; a name under 4 letters never is."""
    stem = os.path.splitext(os.path.basename(path))[0]
    if len(stem) < 4:
        return {}
    word = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(stem)}(?![A-Za-z0-9_])")
    out = {}
    for name, text in texts.items():
        if not word.search(text):
            continue
        parts = text.splitlines()
        if name.endswith(".py"):
            try:
                parts = [ast.get_source_segment(text, node) or "" for node in ast.parse(text).body]
            except (SyntaxError, ValueError):
                pass
        found = sorted(p for p in parts if word.search(p))
        if found:
            out[name] = found
    return out


def codex_since(session, codex_root):
    """What changed in the codex since the last run worked from it (codex.seen), for the briefing; None before any
    run recorded its codex.

    A change to a file that notes.md or lib.py names stays listed, run after run, until a run told of it edits the
    note or helper that names it (finish() decides): what was written about the file is then checked. Other changes
    are told once. Kept on the session for finish(): the codex now (codex_seen), the named changes not checked yet
    (codex_pending: {path: {"since": the run whose codex it changed after, "kind"}}) and, of those, the ones this
    briefing listed, with their passages as they are now (codex_told)."""
    if not os.path.isdir(codex_root):
        return None
    now = session.codex_seen = codexmod.fingerprint(codex_root)
    last = codexmod.seen(session.root)
    if not last or not now:
        return None
    texts = _written(session.root)
    diff = codexmod.changes(last["files"], now)
    kind_of = {p: kind for kind in ("changed", "removed", "added") for p in diff[kind]}
    pending = {}
    for path, entry in (last.get("pending") or {}).items():  # (told before, not checked since)
        if isinstance(entry, dict) and passages(path, texts):
            pending[path] = {"since": entry.get("since"), "kind": kind_of.get(path, entry.get("kind") or "changed")}
    for path, kind in kind_of.items():
        if path not in pending and kind != "added" and passages(path, texts):
            pending[path] = {"since": last.get("run"), "kind": kind}
    session.codex_pending = pending
    session.codex_told = {}
    head = f"## The codex since the last run (runs/{last.get('run')})"
    if not kind_of and not pending:
        return f"{head}\nUnchanged: the same code and data as that run worked from."

    entries = []  # (order, path, line): new changes named in notes.md or lib.py, then earlier ones, then the rest
    for kind in ("changed", "removed", "added"):
        for path in sorted(p for p in set(kind_of) | set(pending) if (pending.get(p) or {"kind": kind_of[p]})["kind"]
                           == kind):
            entry = pending.get(path)
            if entry is None:
                entries.append((2, path, f"- {kind}: codex/{path}"))
                continue
            earlier = entry["since"] != last.get("run")
            names = ", ".join(passages(path, texts))
            entries.append((1 if earlier else 0, path, f"- {kind}: codex/{path} (named in {names}"
                            + (f"; changed before runs/{entry['since']}, not checked since)" if earlier else ")")))
    entries.sort(key=lambda e: e[0])
    shown = entries[:BRIEF_CHANGES]
    session.codex_told = {path: passages(path, texts) for _, path, _ in shown if path in pending}
    earlier = sum(1 for e in pending.values() if e["since"] != last.get("run"))
    counts = ", ".join(f"{len(diff[k])} {k}" for k in ("changed", "removed", "added") if diff[k])
    summary = "; ".join(filter(None, [
        counts and f"{counts} since that run",
        earlier and f"{earlier} earlier changes to files notes.md or lib.py name, not checked since"]))
    text = [head, f"{summary}. What notes.md, lib.py and earlier harnesses say about these may be out of date: check "
            "it before you rely on it. A file notes.md or lib.py names stays listed until the note or helper naming "
            f"it is edited: fix it, or, if it still holds, say so there (\"checked in runs/{session.run_id}\")."]
    text += [line for *_, line in shown]
    if len(entries) > BRIEF_CHANGES:
        text.append(f"... {len(entries) - BRIEF_CHANGES} more")
    return "\n".join(text)


def _show(v):
    try:
        return json.dumps(runtime.jsonable(v), indent=1)[:8000]
    except Exception:
        return repr(v)[:8000]


def _cut(text, n):
    return text if len(text) <= n else text[: n // 2] + f"\n... ({len(text) - n} characters cut) ...\n" + text[-n // 2:]


def _cut_lines(text, n):
    """text's head of at most n characters, cut on a line boundary where one is near, saying what was cut."""
    if len(text) <= n:
        return text
    at = text.rfind("\n", n // 2, n)
    at = at if at > 0 else n
    return text[:at] + f"\n... ({len(text) - at} more characters)"


def stage_of(text):
    """The feed's stage a progress line names ("analyze", "probe", "build", "play", "results"), or None when the
    line names none (an option's "Now: ...", a steer)."""
    if text in STAGES:
        return STAGES[text]
    if str(text).startswith(PLAYING):
        return "play"
    return None


def open_session(game, project, prompt, run_id=None, say=None, client=None, timeout=None, stop=None,
                 steering=None, play_seconds=None, describer=None, activity=None):
    """A Session for one request, ready for an agent: its budget and stop set, and the sandbox started when
    AUTOPILOT_SANDBOX asks for one (autopilot/sandbox.py). The arguments are those of an agent's implement();
    play_seconds: an exploration's minutes of play, in seconds (its episodes end once they are up, and the run
    PLAY_GRACE later); describer: the run's describe_frames (autopilot/vision.py; None: not in this run);
    activity: where the run's activity events go (the plugin's link; docs/protocol.md, `activity`)."""
    from .. import sandbox as sandboxmod

    session = Session(game, project, prompt, run_id or time.strftime("%Y%m%d-%H%M%S"), say=say, client=client,
                      activity=activity)
    session.budget = float(timeout or default_timeout())
    session.play_seconds = float(play_seconds) if play_seconds else None
    stopped = stop or (lambda: False)
    session.stop = lambda: bool(stopped()) or session.out_of_time()
    if steering is not None:
        session.steering = steering
    session.describer = describer
    # The run's earlier session, when one stopped before it reported and this one goes on from it (go_on): its
    # episodes keep their numbers (harnessN.py, traceN_summary.txt), and the agent is told to read them first
    marker = os.path.join(session.run_dir, GOING_ON_FILE)
    session.going_on = os.path.exists(marker)
    if session.going_on:
        try:
            with open(marker, encoding="utf-8") as f:
                session.episodes = int(json.load(f).get("episodes") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        with contextlib.suppress(OSError):
            os.remove(marker)
    if sandboxmod.mode() not in ("off", "child"):
        session.sandbox = sandboxmod.Sandbox(game.link, client, project, prompt, session.run_id, session.root,
                                             codexmod.MEMORY, scrub=scrub,
                                             recorder=getattr(describer, "recorder", None),
                                             level=getattr(game, "level", None), say=session.say,
                                             activity=session.activity)
        session.note(f"sandbox: {session.sandbox.kind}")
    return session


def first_message(session):
    """The request message an agent starts from: the request, then the briefing (what every run would read first,
    and what the live game shows now). Also saves the request as prompt.txt in the run's folder."""
    opening = None
    try:
        opening = session.opening()
        session.note(f"opening: {opening[:1500]}")
    except Exception as e:
        session.note(f"opening failed: {e}")
    with open(os.path.join(session.run_dir, "prompt.txt"), "w", encoding="utf-8") as f:
        f.write(session.prompt)
    going_on = GOING_ON.format(run=session.run_rel) + "\n\n" if getattr(session, "going_on", False) else ""
    return PROMPT.format(prompt=session.prompt, project=session.project, run=session.run_rel,
                         briefing=going_on + briefing(session, opening) + "\n\n" + session.looking_note())


def watch_steering(session):
    """While the user's words wait to be read, end the episode running in the sandbox (the sandbox can't see them).
    Returns an Event: set it once the agent has finished."""
    done = threading.Event()

    def watch():
        while not done.is_set():
            if session.sandbox and session.steering.pending():
                try:
                    session.sandbox.end_episode()
                except Exception:
                    pass
            done.wait(0.2)

    threading.Thread(target=watch, daemon=True).start()
    return done


def finish(session, started, stopped, last_text, **facts):
    """The request's report: what the agent reported (or, when it reported nothing, a fallback saying why), with the
    run's facts added. Saved as report.json in the run's folder. started: time.time() when the agent started;
    stopped: why the agent ended early (None when it ended by itself); last_text: the agent's last words; facts: the
    agent's own (model, cost, turns...)."""
    if session.out_of_time():  # (an exploration ended by its minutes of play, not by the user)
        stopped = f"its {session.play_seconds / 60:.0f} minutes of play were up"
    report = dict(session.report or {
        "outcome": ("explored" if session.out_of_time() else
                    "INCONCLUSIVE" if re.match(r"(?i)\s*verify", session.prompt) else "not_achieved"),
        "summary": f"{NO_REPORT} ({stopped or 'the agent ended without one'}). Last words: {last_text[:500]}",
        "evidence": []})
    writes = runtime.jsonable(getattr(session, "sandbox_writes", None) or session.game.writes) or []
    report.update({
        "run": session.run_id, "project": session.project, "prompt": session.prompt,
        "seconds": round(time.time() - started, 1), "episodes": session.episodes,
        "tool_seconds": round(session.tool_seconds, 1), "stopped": stopped,
        # every direct change to the game's state (not player input) made in episodes (level_modified) and, apart,
        # those research made in probes (the level was restarted before play; they never bear on the outcome)
        "level_modified": [w for w in writes if not (isinstance(w, dict) and w.get("probe") and not w.get("in_play"))],
        "research_modified": [w for w in writes if isinstance(w, dict) and w.get("probe") and not w.get("in_play")],
        **facts,
    })
    if session.alerts:
        report["alerts"] = list(session.alerts)
    with open(os.path.join(session.run_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    # The codex this run worked from: the next run is told what changed since. Only a run that played or reported
    # counts, so changes a run stopped (or crashed) before it worked on them are told to the next one. A named change
    # this run was told of is checked once the passages naming it were edited (or none names it any more); the rest
    # stay listed
    if getattr(session, "codex_seen", None) and (session.report or session.episodes):
        try:
            after, told = _written(session.root), getattr(session, "codex_told", None) or {}
            pending = {}
            for path, entry in (getattr(session, "codex_pending", None) or {}).items():
                now = passages(path, after)
                if now and (path not in told or now == told[path]):
                    pending[path] = entry
            codexmod.record_seen(session.root, session.run_id, session.codex_seen, pending)
        except OSError as e:
            session.note(f"couldn't record the codex this run saw: {e}")
    session.note(f"done: {report.get('outcome')} in {report['seconds']}s")
    return report


def go_on(run_dir, report):
    """The next session opened in run_dir (open_session) goes on from the one that wrote `report`, rather than
    starting the run over."""
    with open(os.path.join(run_dir, GOING_ON_FILE), "w", encoding="utf-8") as f:
        json.dump({"episodes": int(report.get("episodes") or 0)}, f)


def cut_off(report):
    """Whether the run's agent stopped before it reported, by itself: not stopped by the user, nor out of its time or
    its minutes of play (finish's report)."""
    return (str(report.get("summary") or "").startswith(NO_REPORT) and report.get("outcome") != "explored"
            and report.get("stopped") not in ("stopped", "timed out"))


# Environment variables whose values are secrets: kept from model-written code and from what the plugin is sent
SECRET = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|DATABASE_URL", re.I)


def scrub(value):
    """value with every secret's value replaced by [secret] (strings, dicts and lists, recursively). Applied to all
    that goes to the plugin from model-influenced code: status lines, inspector frames, the report."""
    secrets = [v for v in (os.environ.get(k, "") for k in os.environ if SECRET.search(k)) if v and len(v) >= 8]
    from ..redact import _gateway_key

    secrets += [v for v in [_gateway_key()] if v and v not in secrets]  # (kept in gateway.env, not the environment)
    if not secrets:
        return value
    if isinstance(value, str):
        for v in secrets:
            value = value.replace(v, "[secret]")
        return value
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    return value
