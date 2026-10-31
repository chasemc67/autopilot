"""Build the Roblox Studio plugin as one file, Autopilot.server.lua, for Studio's Plugins folder (no Rojo needed).

Studio loads a single .lua/.luau file in the Plugins folder as a plugin Script. This puts every module of src/ into it
as a function (`require(script.Parent.X)` becomes `__require("X")`), keeps their source texts too (the client agent the
plugin copies into the place needs them as real ModuleScripts: Sources.luau is replaced by those texts), and bakes in
the bridge token when one is given (`autopilot roblox install` does).

    python plugin/roblox/build_plugin.py [--out Autopilot.server.lua] [--token <token>] [--url <bridge url>]
"""

import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "src")
ENTRY = "Main.server.luau"
TOKEN_PLACEHOLDER = "__AUTOPILOT_ROBLOX_TOKEN__"
DEFAULT_URL = "http://127.0.0.1:8766/v1/roblox"
REQUIRE = re.compile(r"require\(\s*script\.Parent\.(\w+)\s*\)")
EXPORT = re.compile(r"^export type ", re.M)
# Source texts only (the plugin copies them into the place), never required by the plugin itself
SOURCE_ONLY = ("ClientAgent",)


def modules(src=SRC):
    """{name: source} of the plugin's modules (every .luau but the entry), in name order."""
    out = {}
    for f in sorted(os.listdir(src)):
        if f.endswith(".luau") and f != ENTRY:
            with open(os.path.join(src, f), encoding="utf-8") as fh:
                out[f[: -len(".luau")]] = fh.read()
    return out


def long_string(text):
    """text as a Lua long string ([==[...]==]) whose level doesn't occur in it."""
    level = 0
    while f"]{'=' * level}]" in text:
        level += 1
    eq = "=" * level
    # (a long string drops a newline right after its opening: one is added so the text keeps its own first line)
    return f"[{eq}[\n{text}]{eq}]"


def as_body(text):
    """A module's text as a function's body: without its --!strict line (a mode comment counts only at the top of a
    script), its `export type`s as plain ones (export is for a module's top level), its requires through __require."""
    text = re.sub(r"\A\s*--!\w+[^\n]*\n", "", text)
    text = EXPORT.sub("type ", text)
    return REQUIRE.sub(lambda m: f'__require("{m.group(1)}")', text)


def build(token=None, url=None, src=SRC):
    """The single-file plugin's text."""
    mods = modules(src)
    if "Config" in mods:
        if token:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", token):
                raise ValueError("the token may hold letters, digits, - and _ only")
            mods["Config"] = mods["Config"].replace(TOKEN_PLACEHOLDER, token)
        if url:
            if '"' in url or "\n" in url:
                raise ValueError("bad url")
            mods["Config"] = mods["Config"].replace(f'"{DEFAULT_URL}"', f'"{url}"')
    with open(os.path.join(src, ENTRY), encoding="utf-8") as f:
        entry = f.read()
    names = set(mods)
    for name, text in [*mods.items(), (ENTRY, entry)]:
        for used in REQUIRE.findall(text):
            if used not in names:
                raise ValueError(f"{name} requires {used}, which isn't in src/")
    lines = [
        "--!nocheck",
        "-- Autopilot for Roblox Studio: the plugin in one file (built by plugin/roblox/build_plugin.py from",
        "-- plugin/roblox/src; don't edit, rebuild). Put it in Studio's Plugins folder.",
        "",
        "local __SOURCES = {}",
    ]
    # The texts the client agent is made from (Sources.get): kept as they are in src/, real requires and all
    for name, text in mods.items():
        if name in ("Sources", "Config"):  # (never copied into the place: the token stays in the plugin)
            continue
        lines.append(f'__SOURCES["{name}"] = {long_string(text)}')
    lines += [
        "",
        "local __MODULES = {}",
        "local __LOADED = {}",
        "local function __require(name)",
        "\tlocal loaded = __LOADED[name]",
        "\tif loaded == nil then",
        "\t\tlocal make = __MODULES[name]",
        "\t\tif make == nil then",
        '\t\t\terror("Autopilot: no module " .. tostring(name))',
        "\t\tend",
        "\t\tloaded = make()",
        "\t\t__LOADED[name] = loaded",
        "\tend",
        "\treturn loaded",
        "end",
        "",
    ]
    for name, text in mods.items():
        if name in SOURCE_ONLY:
            continue
        if name == "Sources":
            body = "local Sources = {}\nfunction Sources.get(name)\n\treturn __SOURCES[name]\nend\nreturn Sources\n"
        else:
            body = as_body(text)
        lines.append(f'__MODULES["{name}"] = function()')
        lines.append(body.rstrip("\n"))
        lines.append("end")
        lines.append("")
    lines.append("-- The entry (Main.server.luau)")
    lines.append(as_body(entry).rstrip("\n"))
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.join(HERE, "build", "Autopilot.server.lua"))
    ap.add_argument("--token", help="the bridge token to bake in (default: none; enter it in the dock)")
    ap.add_argument("--url", help=f"the bridge's address (default: {DEFAULT_URL})")
    args = ap.parse_args(argv)
    text = build(args.token, args.url)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(f"Built {args.out} ({len(text)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
