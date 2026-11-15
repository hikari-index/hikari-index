"""Compare the compose files, their .env examples and the programs with
settings.json.

    python tests/settings/check.py

Prints each difference it finds and exits 1 if there is one. Standard
library only; CI runs it before any image is built.

settings.json is the one list of settings a person can set: one name and
one default each. The wrappers were written at different times and
drifted: compose.yaml set the workers' poll interval, the second-machine
files left the program's different default, and the docs described only
the first. This reads the files as text (it is not a YAML or a language
parser) and checks:

  compose.yaml, compose.cpu-worker.yaml, compose.rtx-worker.yaml
    - every ${NAME} is a setting the list gives that route, written as
      ${NAME:-default} with the list's default (or ${NAME:?message} for
      one with no default);
    - a setting a program reads is handed to it under the name the list
      says, as that ${...} and nothing else, so neither a literal value
      nor a renamed variable gets past;
    - compose.yaml uses every setting the list gives it.
  .env.example, .env.cpu-worker.example, .env.rtx-worker.example,
  gallery/scripts/first-run.js
    - the names in them are that route's settings and no others (the
      values in an example are examples, and are not compared).
  containers/all-in-one/scripts
    - the settings its start scripts take from the container are that
      route's and no others, all of them, with the list's defaults.
  worker/, providers/, gallery/src, gallery/scripts
    - every environment name read is in the list, as that program's
      setting or as wiring; every setting is read by the program the
      list names; a default written beside a read is the list's, and a
      Python program states the default of each setting that has one.

Not checked: whether a compose file is otherwise sound (a service that
lost its environment block), values set in a Dockerfile, compose.db.yaml
(a developer's database), and anything a real run would show. The
side-by-side run of the install routes is the proof; this catches a name
or a default changed in one place.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIST = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))
SETTINGS = LIST["settings"]
WIRING = set(LIST["wiring"])
# Read by the runtime, not by the program's own code: ORIGIN by the
# gallery's server adapter, TZ by the C library.
RUNTIME = {"ORIGIN", "TZ"}

INTERPOLATION = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])([^}]*))?\}")
BARE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
KEY_VALUE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*):\s+(\S.*)$")
ENV_LINE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.M)
# os.environ.get("NAME", "default"), os.environ.get("NAME") or "default",
# os.environ["NAME"], os.getenv(...) the same ways.
PY_READ = re.compile(
    r"""(?:environ(?:\.get\(|\[)|getenv\()\s*["']([A-Z][A-Z0-9_]*)["']"""
    r"""(?:\s*,\s*["']([^"']*)["']\s*\)|\s*\)\s*or\s*["']([^"']*)["'])?"""
)
# env.NAME, env["NAME"], process.env.NAME, with || "default" or ?? "default"
JS_READ = re.compile(
    r"""\benv(?:\.([A-Z][A-Z0-9_]*)|\[\s*["']([A-Z][A-Z0-9_]*)["']\s*\])"""
    r"""(?:\s*(?:\|\||\?\?)\s*["']([^"']*)["'])?"""
)

problems: list[str] = []


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def name_on(setting: dict, route: str) -> str:
    return setting.get("names", {}).get(route, setting["name"])


def on_route(route: str) -> dict[str, dict]:
    return {name_on(s, route): s for s in SETTINGS if route in s["routes"]}


def check_compose(path: str, route: dict[str, dict]) -> set[str]:
    """One compose file against its route's settings; returns the names it reads."""
    # program name -> the setting that must feed it
    feeds = {target: (name, s) for name, s in route.items() for target in s["reads"].values()}
    seen: set[str] = set()
    for number, raw in enumerate(text(path).splitlines(), 1):
        line = re.sub(r"(^|\s)#.*$", "", raw).replace("$$", "")
        where = f"{path}:{number}"
        found = INTERPOLATION.findall(line)
        for name in BARE.findall(INTERPOLATION.sub("", line)):
            problems.append(f"{where} reads ${name} without braces; write ${{{name}:-default}} so the default is stated")
        pair = KEY_VALUE.match(line)
        key, value = (pair.group(1), pair.group(2).strip().strip('"')) if pair else (None, None)
        for name, operator, default in found:
            seen.add(name)
            setting = route.get(name)
            if setting is None:
                problems.append(f"{where} reads {name}, which settings.json does not list for this route")
                continue
            wanted = setting["default"]
            if operator == ":?" and not wanted:
                pass
            elif operator != ":-":
                problems.append(f"{where} reads {name} as ${{{name}{operator}...}}; write ${{{name}:-{wanted}}} so unset and empty both get the default")
            elif default != wanted:
                problems.append(f"{where} gives {name} the default {default!r}; settings.json says {wanted!r}")
            targets = set(setting["reads"].values())
            if targets and (key not in targets or INTERPOLATION.fullmatch(value) is None):
                problems.append(f"{where} uses {name} somewhere other than as the whole value of {' or '.join(sorted(targets))}, the name its program reads")
        if key in feeds:
            name, _ = feeds[key]
            if [f[0] for f in found] != [name] or INTERPOLATION.fullmatch(value) is None:
                problems.append(f"{where} sets {key} to {value!r}; it must be ${{{name}}} with the list's default, nothing else")
    return seen


def same_names(where: str, found: set[str], wanted: set[str]) -> None:
    for name in sorted(found - wanted):
        problems.append(f"{where} has {name}, which settings.json does not list for it")
    for name in sorted(wanted - found):
        problems.append(f"settings.json lists {name} for {where}, which never mentions it")


# --- one machine: compose.yaml, .env.example, the first-run script ----------
compose = on_route("compose")
seen = check_compose("compose.yaml", compose)
for name in sorted(set(compose) - seen):
    if not compose[name].get("compose_own"):
        problems.append(f"settings.json lists {name} for compose.yaml, which never reads it")
made = {name for name, s in compose.items() if s["group"] == "made"}
same_names(".env.example", set(ENV_LINE.findall(text(".env.example"))), set(compose) - made)
same_names("gallery/scripts/first-run.js", set(ENV_LINE.findall(text("gallery/scripts/first-run.js"))), made)

# --- a second machine's worker ---------------------------------------------
# Its compose file hands the settings file straight to the worker, and the
# two kinds (cpu, rtx) do not take all the same settings, so each must name
# only this route's settings and the two together must name all of them.
second = on_route("second-machine")
mentioned: set[str] = set()
for kind in ("cpu", "rtx"):
    names = check_compose(f"compose.{kind}-worker.yaml", second)
    example = set(ENV_LINE.findall(text(f".env.{kind}-worker.example")))
    for name in sorted(example - set(second)):
        problems.append(f".env.{kind}-worker.example has {name}, which settings.json does not list for a second machine")
    mentioned |= names | example
for name in sorted(set(second) - mentioned):
    problems.append(f"settings.json lists {name} for a second machine, and neither worker's files mention it")

# --- the all-in-one image ---------------------------------------------------
# Its start scripts take the settings from the one container's variables
# and hand each program its own. Checked here: the names the scripts take
# are this route's and no others, every one of the route's is taken, and
# a default written in a script is the list's. That each program then
# gets the right ones under the right names is shown by running it.
aio = on_route("all-in-one")
SHELL = re.compile(r"\$\{?(HIKARI_[A-Z0-9_]+|PUID|PGID|TZ|ORIGIN)\b(?:(:?[-+?])([^}$]*)\})?")
taken: set[str] = set()
for path in sorted((ROOT / "containers/all-in-one/scripts").iterdir()):
    where = path.relative_to(ROOT).as_posix()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if raw.lstrip().startswith("#"):
            continue
        for name, operator, default in SHELL.findall(raw):
            taken.add(name)
            if name in aio:
                wanted = aio[name].get("default_on", {}).get("all-in-one", aio[name]["default"])
                if operator == ":-" and default != wanted:
                    problems.append(f"{where}:{number} gives {name} the default {default!r}; settings.json says {wanted!r}")
            elif name not in WIRING and name not in RUNTIME:
                problems.append(f"{where}:{number} takes {name}, which settings.json does not list for the all-in-one")
for name in sorted(set(aio) - taken):
    problems.append(f"settings.json lists {name} for the all-in-one, and none of its start scripts takes it")

# --- the programs -----------------------------------------------------------
# (program, the name it reads) -> the setting
expected: dict[tuple[str, str], dict] = {}
for s in SETTINGS:
    for program, name in s["reads"].items():
        expected[(program, name)] = s
known = {name for _, name in expected} | WIRING


def program_of(where: str) -> str | None:
    if where.startswith("gallery/"):
        return "gallery"
    if where == "worker/hikari_worker/analyze.py":
        return "analyze"
    if where.startswith("worker/"):
        return "worker"
    return None  # providers run inside both workers


# (program, name) -> the defaults written beside its reads (None: none written)
stated: dict[tuple[str, str], list[tuple[str, str | None]]] = {}
for folder, suffixes, reader in (("worker", {".py"}, PY_READ), ("providers", {".py"}, PY_READ),
                                 ("gallery/src", {".js", ".ts", ".svelte"}, JS_READ), ("gallery/scripts", {".js"}, JS_READ)):
    for path in sorted((ROOT / folder).rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        where = path.relative_to(ROOT).as_posix()
        program = program_of(where)
        for match in reader.finditer(path.read_text(encoding="utf-8", errors="replace")):
            groups = match.groups()
            if reader is PY_READ:
                name, default = groups[0], groups[1] if groups[1] is not None else groups[2]
            else:
                name, default = groups[0] or groups[1], groups[2]
            if program is not None and (program, name) in expected:
                stated.setdefault((program, name), []).append((where, default))
            elif name in WIRING or (program is None and name in known):
                continue
            elif name in known:
                problems.append(f"{where} reads {name}, which settings.json gives to another program, not the {program}")
            else:
                problems.append(f"{where} reads {name}, which settings.json has neither as a setting nor as wiring")

for (program, name), setting in sorted(expected.items()):
    reads = stated.get((program, name), [])
    if not reads:
        if name not in RUNTIME:
            problems.append(f"settings.json says the {program} reads {name}, and nothing in its code does")
        continue
    wanted = setting["default"]
    for where, default in reads:
        if default is not None and default != wanted:
            problems.append(f"{where} gives {name} the default {default!r}; settings.json says {wanted!r}")
    # The gallery's defaults are often a test ("0 hides it"), not a literal.
    if wanted and program != "gallery" and all(default is None for _, default in reads):
        problems.append(f"the {program} reads {name} without stating its default, {wanted!r}: a wrapper that sets nothing would get none")

if problems:
    print(f"{len(problems)} difference(s) from settings.json:")
    for line in problems:
        print(f"  - {line}")
    sys.exit(1)
print(f"settings.json: {len(SETTINGS)} settings; no difference found in the compose files, the .env examples, the all-in-one's start scripts or the programs' reads")
