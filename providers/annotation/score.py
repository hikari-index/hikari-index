"""Score a run's labels against the owner's checked stills.

The gallery's label report (Admin, Labels) says how often review agreed
with the labels that were reviewed. This answers the next question, before
a change touches the library: would a new model, allowlist or cut-off do
better on those same stills? Download the checks from the report, run the
candidate analyze over the same works, and score its proposals:

    python -m annotation.score --checks label-checks.json \
        runs/<work>/analyze [runs/<other work>/analyze ...]

A work's frames keep their candidate ids across re-runs of analysis (the
extraction is unchanged), which is what lets the two be matched. The
truth is the same rule the report uses: the owner's value where they set
one (null means nothing applies), the reviewed machine value where they
left it as proposed. Output: per label, the reviewed labels and the
candidate side by side, then the candidate per source and value.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

FAMILIES = ("shot_scale", "composition", "people", "setting", "time",
            "weather", "lighting", "color_bias", "saturation", "angle")
# Where each facet sits in a proposal's labels (the gallery's FACET_PATHS).
PATHS = {
    "setting": ("setting_time_weather", "setting"),
    "time": ("setting_time_weather", "time"),
    "weather": ("setting_time_weather", "weather"),
    "shot_scale": ("shot_scale",),
    "people": ("people",),
    "color_bias": ("lighting_color_character", "color_bias"),
    "saturation": ("lighting_color_character", "saturation"),
    "lighting": ("lighting_color_character", "lighting"),
    "composition": ("angle_composition", "composition"),
    "angle": ("angle_composition", "angle"),
}
# Below this many, a share is printed in brackets: too few to tune on.
ENOUGH = 20


#: A label the gallery only suggested (a shot size from a head, #41) and
#: the owner neither accepted nor replaced: no judgment either way, so the
#: still is left out of that family's scoring. "Left as proposed" cannot
#: apply, because nothing was proposed.
UNANSWERED = object()


def truth(check: dict, family: str):
    human = check.get("human") or {}
    if family in human:
        return human[family] or None
    machine = (check.get("machine") or {}).get(family) or None
    if machine is None and family in (check.get("suggested") or {}):
        return UNANSWERED
    return machine


def _label(labels: dict, family: str) -> Optional[str]:
    node = labels
    for key in PATHS[family]:
        node = node.get(key) if isinstance(node, dict) else None
    return node if isinstance(node, str) and node != "abstain" else None


def load_run(path: Path) -> tuple[str, dict[str, dict]]:
    """A run's labels and sources per candidate, from its analyze folder or
    one proposals file; the work is read from the file's run_ref."""
    files = ([path / "proposals.json", path / "surplus-proposals.json"]
             if path.is_dir() else [path])
    work = None
    out: dict[str, dict] = {}
    for file in files:
        if not file.is_file():
            continue
        document = json.loads(file.read_text(encoding="utf-8"))
        ref = document.get("run_ref", "")
        work = work or (ref[len("run-"):] if ref.startswith("run-") else None)
        for entry in document["proposals"]:
            provenance = entry.get("provenance") or {}
            fields = provenance.get("fields") or {}
            labels = entry["proposal"]["labels"]
            record = {}
            for family in FAMILIES:
                source = (fields.get(family) or {}).get("source")
                if family == "shot_scale" and not source:
                    source = provenance.get("shot_scale_lane")
                record[family] = (_label(labels, family), source)
            out[entry["candidate_id"]] = record
    if work is None:
        raise ValueError(f"{path}: no proposals file with a run_ref of the form run-<work>")
    return work, out


def _blank() -> dict:
    return {"answered": 0, "right": 0, "missed": 0, "truths": 0}


def _add(cell: dict, value: Optional[str], expected: Optional[str]) -> None:
    if expected:
        cell["truths"] += 1
    if value:
        cell["answered"] += 1
        cell["right"] += value == expected
    elif expected:
        cell["missed"] += 1


def score(checks: Iterable[dict], runs: dict[str, dict[str, dict]]) -> dict:
    """Per family: the reviewed labels ("reviewed") and the candidate run
    ("candidate") over the same checked stills, plus the candidate per
    source and value. Checked stills the runs do not cover are counted."""
    result = {family: {"reviewed": _blank(), "candidate": _blank(), "by_value": {}}
              for family in FAMILIES}
    matched = unmatched = 0
    for check in checks:
        run = runs.get(check.get("work"))
        record = run.get(check.get("candidate")) if run else None
        if record is None:
            unmatched += 1
            continue
        matched += 1
        for family in FAMILIES:
            expected = truth(check, family)
            if expected is UNANSWERED:
                continue
            _add(result[family]["reviewed"],
                 (check.get("machine") or {}).get(family) or None, expected)
            value, source = record[family]
            _add(result[family]["candidate"], value, expected)
            if value:
                key = f"{source or 'not recorded'} / {value}"
                cell = result[family]["by_value"].setdefault(key, _blank())
                _add(cell, value, expected)
    return {"matched": matched, "unmatched": unmatched, "families": result}


def _share(right: int, n: int) -> str:
    if not n:
        return "-"
    text = f"{round(100 * right / n)}%"
    return text if n >= ENOUGH else f"({text})"


def report(result: dict) -> str:
    lines = [f"{result['matched']} checked stills scored"
             + (f"; {result['unmatched']} not in these runs" if result["unmatched"] else ""),
             "right = share of proposed labels the review agrees with; "
             "answered = share of stills with a reviewed value that got one; "
             f"(brackets) = fewer than {ENOUGH}", ""]
    for family, cells in result["families"].items():
        lines.append(family)
        for name in ("reviewed", "candidate"):
            c = cells[name]
            answered = c["truths"] - c["missed"]
            lines.append(f"  {name:<10} proposed {c['answered']:>4}  right {_share(c['right'], c['answered']):>6}"
                         f"  answered {_share(answered, c['truths']):>6} of {c['truths']}")
        for key, c in sorted(cells["by_value"].items()):
            lines.append(f"    {key:<36} {c['answered']:>4}  right {_share(c['right'], c['answered']):>6}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="annotation-score", description=__doc__.split("\n\n")[0])
    parser.add_argument("--checks", required=True,
                        help="label-checks.json downloaded from the gallery's label report")
    parser.add_argument("runs", nargs="+",
                        help="a work's analyze folder (or one proposals file) per work")
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    args = parser.parse_args(argv)
    document = json.loads(Path(args.checks).read_text(encoding="utf-8"))
    if document.get("format") != "hikari-label-checks-1":
        raise ValueError(f"{args.checks} is not a label-checks download from the gallery")
    runs = dict(load_run(Path(p)) for p in args.runs)
    result = score(document["checks"], runs)
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else report(result))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"annotation-score: {error}", file=sys.stderr)
        sys.exit(1)
