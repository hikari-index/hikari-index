"""Opening and ending intervals from a file's chapter markers (#47).

Many releases carry Matroska chapters so a player can skip the opening and
the ending. Read with ffprobe, they become *proposed* exclusion intervals
(source `chapter_marker`): the sampler then extracts the frames inside
them and holds them aside instead of offering them to the picker. Nothing
is dropped; a held frame sits in the surplus like any other pool frame and
a lock brings it in.

Which chapters count, measured on the maintainer's library (139 files,
2026-10-09): 121 carry chapters, 90 name the opening or ending outright,
and the names are per release group, not a standard. "Intro", "Avant" and
"Prologue" are the cold open in one show and the opening in another, so
they never count by name. A chapter 85 to 95 seconds long sitting in the
first or last few minutes is an opening or ending in every release looked
at, named or not (it recovers a show whose chapters are numbered and one
whose opening is called "Intro"), so length and position count too. A
zero-length "OP" and an eight-second "Opening" exist; a chapter shorter
than MIN_SECONDS is ignored whatever it is called.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any, Optional

from .exclusion import ExclusionInterval

#: Chapter titles that name an opening or an ending, matched at the start of
#: the title, case-insensitively, as a whole word ("OP", "OP 1", "Opening",
#: "ED", "Ending", "Outro", "Credits", "Credits Start").
OPENING_NAMES = re.compile(r"^(op|opening)(\b|\d)", re.IGNORECASE)
ENDING_NAMES = re.compile(r"^(ed|ending|outro|credits?)(\b|\d)", re.IGNORECASE)
#: A chapter this long near either end of the file is an opening or ending
#: whatever it is called (85-95 s: the one length every release used).
THEME_SECONDS = (85.0, 95.0)
#: "Near either end": the opening starts, or the ending ends, within this
#: many seconds of the file's start or end. The longest cold open seen
#: before an opening was 333 s (one unnamed, found by this rule).
EDGE_SECONDS = 360.0
#: A named chapter shorter than this is a marker, not a theme (seen: 0 s, 8 s).
MIN_SECONDS = 20.0
#: A hold must leave the pick something: a chapter, or all held chapters
#: together, that would leave less than this much of the file unheld is
#: not an opening or ending whatever it is called (a whole-file "OP", a
#: film chaptered as one long theme). A 90 s opening in a 150 s short
#: still holds, with a minute left to pick from.
MIN_OPEN_SECONDS = 60.0


def probe_chapters(video_path: str, ffprobe_path: str) -> tuple[list[dict], float]:
    """The file's chapters as ffprobe reports them, and its duration.

    Each chapter: {"start", "end", "title"} in seconds; an untitled chapter has
    title "". A file with no chapters gives an empty list, not an error.
    """
    result = subprocess.run(
        [ffprobe_path, "-v", "error", "-print_format", "json", "-show_chapters",
         "-show_entries", "format=duration", "--", video_path],
        capture_output=True, text=True, check=True,
    )
    document = json.loads(result.stdout or "{}")
    chapters = []
    for entry in document.get("chapters", []):
        try:
            start, end = float(entry.get("start_time", 0.0)), float(entry.get("end_time", 0.0))
        except (TypeError, ValueError):
            continue
        chapters.append({"start": start, "end": end,
                         "title": str((entry.get("tags") or {}).get("title", "") or "")})
    try:
        duration = float((document.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return chapters, duration


def classify_chapter(chapter: dict, duration: float) -> Optional[tuple[str, str, str]]:
    """(reason, confidence, rule) for a chapter that reads as a theme, else None.

    reason is `opening_theme` or `ending_theme`; confidence `high` when the
    title says so, `medium` when only the length and position do; rule names
    which.
    """
    title = (chapter.get("title") or "").strip()
    length = float(chapter["end"]) - float(chapter["start"])
    if length < MIN_SECONDS:
        return None
    if duration > 0 and duration - length < MIN_OPEN_SECONDS:
        return None
    if OPENING_NAMES.search(title):
        return ("opening_theme", "high", "named")
    if ENDING_NAMES.search(title):
        return ("ending_theme", "high", "named")
    if THEME_SECONDS[0] <= length <= THEME_SECONDS[1] and duration > 0:
        if float(chapter["start"]) <= EDGE_SECONDS:
            return ("opening_theme", "medium", "length-and-position")
        if duration - float(chapter["end"]) <= EDGE_SECONDS:
            return ("ending_theme", "medium", "length-and-position")
    return None


def chapter_intervals(chapters: list[dict], duration: float, run: str) -> tuple[list[ExclusionInterval], list[dict]]:
    """Proposed exclusion intervals for the chapters that read as themes, and
    an audit row per chapter saying what was decided and why.

    Holds nothing, and marks every row `held: false` with a `skipped`
    reason, when the chapters that read as themes would together leave
    less than MIN_OPEN_SECONDS of the file unheld: that is not an opening
    and an ending, it is a chaptering the rule does not understand, and
    the pick must keep something to choose from.
    """
    intervals: list[ExclusionInterval] = []
    audit: list[dict[str, Any]] = []
    for number, chapter in enumerate(chapters, start=1):
        verdict = classify_chapter(chapter, duration)
        row = {"chapter": number, "title": chapter.get("title", ""),
               "start_seconds": round(float(chapter["start"]), 3),
               "end_seconds": round(float(chapter["end"]), 3), "held": verdict is not None}
        if verdict is not None:
            reason, confidence, rule = verdict
            interval = ExclusionInterval(
                interval_id=f"chapter-{number:02d}",
                start_seconds=float(chapter["start"]),
                end_seconds=float(chapter["end"]),
                reason=reason, source="chapter_marker", confidence=confidence,
                run=run, status="exclusion_proposed",
            )
            intervals.append(interval)
            row.update({"interval_id": interval.interval_id, "reason": reason,
                        "confidence": confidence, "rule": rule})
        audit.append(row)
    held_seconds = sum(iv.end_seconds - iv.start_seconds for iv in intervals)
    if duration > 0 and duration - held_seconds < MIN_OPEN_SECONDS:
        for row in audit:
            if row["held"]:
                row["held"] = False
                row["skipped"] = "the chapters that read as themes would leave almost nothing to pick from; nothing held"
        return [], audit
    return intervals, audit
