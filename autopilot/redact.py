"""What is taken out of a run's data before it is shared (autopilot/dataset.py, docs/run-data.md).

    secrets    API keys and tokens by their shape (Anthropic, OpenAI, Vercel AI Gateway, Aura's run and install
               tokens, Google, AWS, GitHub, Slack, JWTs, PEM private keys, `Bearer ...`), the values of this machine's
               secret environment variables and its gateway key (autopilot/gateway.py, wherever it is kept), and any
               value assigned to a secret-sounding name (`SecurityToken=...`, `"api_key": "..."`): each becomes
               `[secret]`
    path       the project's folder in a path (as written in text and in JSON, in any letter case): `<project>`,
               keeping what follows (`<project>/Assets/Scripts/Player.cs`)
    home       the user's home folder in a path (`C:\\Users\\<name>`, `/Users/<name>`, `/home/<name>`): `~`
    path       any other absolute path that may say where the user keeps things (`X:\\...`, `\\\\server\\share\\...`,
               `/Volumes/...`, `/mnt/...`, `/media/...`, `/opt/...`, `/srv/...`, `/data/...`): `[path]` and its last
               part (`[path]/Player.cs`). System folders (`C:\\Windows`, `Program Files`, `ProgramData`) stay as they
               are, and an engine install keeps the path from its folder on (`[path]/UE_5.4/Engine/...`, Unity Hub's
               `[path]/6000.0.23f1/Editor/...`)
    user, host this machine's user name and computer name, as words: `[user]`, `[host]`
    email      email addresses: `[email]`

Redactor.text() cleans a string; Redactor.data() a JSON value (keys kept, every string cleaned, a secret-sounding
key's string value replaced whole), so JSON stays JSON. `counts` says how much of each was taken out.
"""

import collections
import getpass
import os
import platform
import re

VERSION = 2  # (2: the project's folder and other absolute paths taken out too)
SECRET = "[secret]"

# API keys and tokens, by their shape
TOKENS = re.compile("|".join((
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    r"\bsk-ant-[A-Za-z0-9_-]{16,}",
    r"\bvck_[A-Za-z0-9_-]{16,}",  # (Vercel AI Gateway's keys)
    r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}",
    r"\bgr[ti]1\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_.-]{8,}",
    r"\bAIza[0-9A-Za-z_-]{30,}",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
)), re.S)
BEARER = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]{16,}=*")
# A name that says its value is secret (and isn't a count of tokens: max_tokens, input_tokens...)
SECRET_NAME = re.compile(r"(?i)(api[_-]?key|secret|token|passw(or)?d|credential|private[_-]?key|access[_-]?key|"
                         r"auth[_-]?key|session[_-]?key|client[_-]?key)")
COUNT_NAME = re.compile(r"(?i)(^|_)(max|min|num|n|input|output|total|budget|cache\w*|prompt|completion|reasoning|"
                        r"thinking)_?tokens?$|tokens?_(count|used|limit|per\w*)$|^tokens?$|tokeni[sz]er")
# name = value / name: value / "name": "value" in text, the value at least 8 characters. It matches from the secret
# word on (the rest of the name before it is read back by _assigned: a pattern starting with it would be slow)
ASSIGNED = re.compile(r"""(?ix)
    (?P<word>api[_-]?key|secret|token|passw(?:or)?d|credential|private[_-]?key|access[_-]?key|auth[_-]?key|
             session[_-]?key|client[_-]?key)(?P<rest>[A-Za-z0-9_.-]*)
    (?P<sep>\\?["']?\s*[:=]\s*\\?["']?)
    (?P<value>[^\s"'\\,;)}\]]{8,})""")
NAME_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# Windows (also as JSON writes it, with doubled backslashes), macOS and Linux home folders
HOME = re.compile(r"(?i)\b[A-Z]:(?:\\\\|\\|/)Users(?:\\\\|\\|/)[^\\/\"'\s:*?<>|]+"
                  r"|/Users/[^/\s\"'\\]+|/home/[^/\s\"'\\]+")
# Any other absolute path: from a drive letter or a UNC share, or under a POSIX folder where disks and installs are
# mounted. Its separators: / or backslashes, as many as JSON (in JSON) doubles them to. A folder's name may hold
# spaces (not at its ends); the last part's may not (the text goes on). A path starts the text, a word or a value (or
# follows an escaped \n), not the middle of one: <project>\\Assets
SEP = r"(?:\\+|/)"
PART = r"[^\\/\"'\s:*?<>|]"
STARTS = r"(?:(?<=\\[nrt])|(?<=file://)|(?<![^\s\"'`=(\[{,;]))"
PATH = re.compile(rf"(?i)(?P<root>(?:(?<=\\[nrt])|(?<![A-Za-z0-9_]))[A-Z]:{SEP}|{STARTS}\\{{2,}}(?=\w)"
                  rf"|(?:{STARTS}|(?<=:))/(?:Volumes|mnt|media|opt|srv|data)/)"
                  rf"(?P<rest>(?:{PART}+(?: +{PART}+)*{SEP})*{PART}+{SEP}?)")
# Folders that say nothing about the user, kept as they are
SYSTEM = re.compile(r"(?i)^(windows|program files|program files \(x86\)|programdata)$")
# An engine install's folder (Unreal's UE_5.4, Unity Hub's 6000.0.23f1): the path is kept from it on
ENGINE = re.compile(r"(?i)^(UE_\d+(\.\d+)*|\d{4}\.\d+\.\d+[abfp]\d+)$")
# User names too common to take out of text as words (they stay inside home paths, which go anyway)
GENERIC = {"admin", "administrator", "user", "users", "test", "dev", "home", "guest", "owner", "steam", "unity",
           "game", "games", "player", "root", "build", "runner", "default", "public", "shared"}
SECRET_ENV = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CREDENTIAL|DATABASE_URL", re.I)


def _is_count(name, value):
    return bool(COUNT_NAME.search(name)) or value.replace(".", "").replace("-", "").isdigit()


class Redactor:
    """Cleans text and JSON of what isn't shared. env, user, host: this machine's (by default); project:
    where the run's project is on it (its folder, or Unreal's .uproject), if known."""

    def __init__(self, env=None, user=None, host=None, home=None, project=None):
        mine = env is None
        env = os.environ if env is None else env
        secrets = {v for k, v in env.items() if SECRET_ENV.search(k) and v and len(v) >= 8}
        if mine:  # (this machine's gateway key, wherever it is kept: autopilot/gateway.py)
            secrets |= set(filter(None, [_gateway_key()]))
        self.secrets = sorted(secrets, key=len, reverse=True)
        self.user = _word(_user() if user is None else user)
        self.host = _word(platform.node() if host is None else host)
        # (the longer first: a computer name often holds the user name, JDOE-PC)
        self.words = [(re.compile(rf"(?i)(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])"), tag)
                      for w, tag in sorted(((self.user, "[user]"), (self.host, "[host]")),
                                           key=lambda wt: -len(wt[0] or "")) if w]
        # This machine's home folder wherever it is (not only under Users/ or home/), as written in text and in JSON
        home = os.path.expanduser("~") if home is None else home
        homes = {home, home.replace("\\", "\\\\"), home.replace("\\", "/")} if len(home or "") > 3 else set()
        self.homes = [re.compile(re.escape(h), re.I) for h in sorted(homes, key=len, reverse=True)]
        # The project's folder (Unreal's: the .uproject's) with any separators, as a whole folder: not Farm2 for Farm
        project = (project or "").rstrip("\\/")
        if os.path.splitext(project)[1].lower() == ".uproject" or os.path.isfile(project):
            project = re.sub(r"[\\/]+[^\\/]*$", "", project)  # (its folder, written either way on any system)
        parts = [p for p in re.split(r"[\\/]+", project) if p]
        start = "/" if project.startswith("/") else r"\\{2,}" if project.startswith("\\") else ""  # (POSIX, a share)
        self.project = re.compile(rf"(?i)(?<![\w.\\/-]){start}{SEP.join(map(re.escape, parts))}(?!{PART})") \
            if len(project) > 3 else None
        self.counts = collections.Counter()
        self._cache = {}

    def text(self, s):
        """s with every secret, project and home folder, other absolute path, user and host name and email address
        taken out."""
        if not s:
            return s
        # (a language model's calls each carry the whole conversation so far: the same long strings again and again)
        hit = self._cache.get(s) if len(s) >= 64 else None
        if hit is not None:
            self.counts.update(hit[1])
            return hit[0]
        before = collections.Counter(self.counts)
        out = self._text(s)
        if len(s) >= 64:
            self._cache[s] = (out, self.counts - before)
        return out

    def _text(self, s):
        for v in self.secrets:
            if v in s:
                self.counts["secret"] += s.count(v)
                s = s.replace(v, SECRET)
        s = self._sub(TOKENS, SECRET, s, "secret")
        s = self._sub(BEARER, lambda m: m.group(1) + SECRET, s, "secret")
        s = self._sub(ASSIGNED, self._assigned, s, None)
        if self.project:
            s = self._sub(self.project, "<project>", s, "path")
        for rx in self.homes:
            s = self._sub(rx, "~", s, "home")
        s = self._sub(HOME, "~", s, "home")
        s = PATH.sub(self._path, s)
        s = self._sub(EMAIL, "[email]", s, "email")
        for rx, tag in self.words:
            s = self._sub(rx, tag, s, tag.strip("[]"))
        return s

    def data(self, value, name=""):
        """A JSON value cleaned: every string by text(), and a secret-sounding key's string value replaced whole."""
        if isinstance(value, str):
            if name and SECRET_NAME.search(name) and len(value) >= 8 and not _is_count(name, value) and \
                    value != SECRET:
                self.counts["secret"] += 1
                return SECRET
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(k) if isinstance(k, str) else k: self.data(v, k if isinstance(k, str) else "")
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self.data(v, name) for v in value]
        return value

    def _assigned(self, m):
        start = m.start()
        while start > 0 and m.string[start - 1] in NAME_CHARS:  # (the name's start: max_tokens, SecurityToken)
            start -= 1
        name = m.string[start:m.start()] + m.group("word") + m.group("rest")
        if _is_count(name, m.group("value")) or m.group("value").startswith("["):
            return m.group(0)
        self.counts["secret"] += 1
        return m.group("word") + m.group("rest") + m.group("sep") + SECRET

    def _path(self, m):
        """An absolute path as [path] and its last part (an engine install's: from its folder on), unless it says
        nothing about the user."""
        pieces = re.split(rf"({SEP})", m.group("rest"))  # (folder, separator, folder, ..., the last part or "")
        names = pieces[::2]
        if m.group("root")[0] != "/" and SYSTEM.match(names[0]):  # (C:\Windows\..., D:\Program Files\...)
            return m.group(0)
        self.counts["path"] += 1
        keep = next((i for i, name in enumerate(names) if ENGINE.match(name)), None)
        if keep is None:
            keep = len(names) - 1 if names[-1] or len(names) == 1 else len(names) - 2
        root = m.group("root")
        sep = pieces[2 * keep - 1] if keep else root[2:] if root[1:2] == ":" else "/"
        return "[path]" + sep + "".join(pieces[2 * keep:])

    def _sub(self, rx, to, s, kind):
        if kind is None:
            return rx.sub(to, s)
        out, n = rx.subn(to, s)
        if n:
            self.counts[kind] += n
        return out


def _gateway_key():
    try:
        from .gateway import key

        value = key()
        return value if value and len(value) >= 8 else None
    except Exception:  # noqa: BLE001 - (no settings folder, no dotenv: no key to take out)
        return None


def _user():
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no user name to take out
        return os.path.basename(os.path.expanduser("~"))


def _word(name):
    name = (name or "").strip()
    return name if len(name) >= 4 and name.lower() not in GENERIC else None
