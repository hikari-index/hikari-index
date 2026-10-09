"""Publish scene-guided exact-PTS candidates and palette descriptors."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, List, Optional

from . import bundle as bundle_steps
from . import composition, frame_quality
from .chapters import chapter_intervals, probe_chapters
from .exclusion import ExclusionInterval, intervals_from_dicts
from .sampler import CandidateRecord, FrameSampler, SamplerParams
from .scene_score import PROGRESS_INTERVAL_SECONDS


PIPELINE_VERSION = "raw-color-extract-1.1-best-in-shot"
# v3 deliberately breaks byte parity with v2. The old zimg path had a measured
# channel-dependent truncation bias and forced BT.709 interpretation on every
# source. v3 is verified against an exact-math referee, not against v2 output:
# declared tags choose the conversion, fully untagged sources use the HD/SD
# convention, and combinations outside the frozen rules are refused.
EXTRACTION_POLICY = "tag-derived-to-srgb-rgb24-swscale-v3"
PREPROCESSING_POLICY = "identity-rgb24-v1"
#: The masters' PNG prediction, set here and not left to the encoder's
#: default: FFmpeg 8 changed that default (none -> paeth), which changes
#: every master's bytes and checksum without changing a pixel. `none` is
#: what every master so far was written with. Paeth files are about 40%
#: smaller and take about 0.4 s longer per 1080p frame; switching is a
#: storage decision, not a side effect of an ffmpeg update.
PNG_ENCODER_OPTIONS = ("-pred", "none")
SRGB_PARAMS_SUFFIX = (
    ",setparams=color_primaries=bt709:color_trc=iec61966-2-1"
)
SWSCALE_FLAGS = "accurate_rnd+full_chroma_int+full_chroma_inp"


@dataclass(frozen=True)
class SourceColorInfo:
    """Colour declarations used to choose the frozen extraction conversion."""

    color_range: Optional[str]
    color_space: Optional[str]
    color_transfer: Optional[str]
    color_primaries: Optional[str]
    coded_height: int

    @classmethod
    def from_probe(cls, stream_probe: dict) -> "SourceColorInfo":
        declared = stream_probe.get("declared_color") or {}
        return cls(
            color_range=declared.get("range"),
            color_space=declared.get("space"),
            color_transfer=declared.get("transfer"),
            color_primaries=declared.get("primaries"),
            coded_height=int(stream_probe["height"]),
        )


def _known_tag(value: Optional[str]) -> Optional[str]:
    if value in (None, "", "unknown", "unspecified", "N/A"):
        return None
    return value


def _unsupported_color_error(color: SourceColorInfo) -> ValueError:
    return ValueError(
        "unsupported source color metadata; refusing to guess: "
        f"color_range={color.color_range!r}, "
        f"color_space={color.color_space!r}, "
        f"color_transfer={color.color_transfer!r}, "
        f"color_primaries={color.color_primaries!r}, "
        f"coded_height={color.coded_height!r}"
    )


def color_rule(color: SourceColorInfo) -> str:
    """Return the audit name for the one conversion rule that matches."""

    color_range = _known_tag(color.color_range)
    color_space = _known_tag(color.color_space)
    color_transfer = _known_tag(color.color_transfer)
    color_primaries = _known_tag(color.color_primaries)

    if color.coded_height < 1 or color_range not in (None, "tv", "pc"):
        raise _unsupported_color_error(color)
    if color_transfer in ("smpte2084", "arib-std-b67"):
        return "hdr-bt2390"
    if (
        color_space == "bt2020nc"
        and color_transfer in ("bt2020-10", "bt2020-12")
        and color_primaries == "bt2020"
    ):
        return "sdr-bt2020"
    if (
        color_space in (None, "bt709")
        and color_transfer in (None, "bt709")
        and color_primaries in (None, "bt709")
        and (
            color.coded_height >= 720
            or (color_space, color_transfer, color_primaries)
            == ("bt709", "bt709", "bt709")
        )
    ):
        return "sdr-bt709"
    if (
        color_space == "smpte170m"
        and color_transfer in (None, "bt709", "smpte170m")
        and color_primaries in (None, "smpte170m")
    ):
        return "sdr-bt601"
    if (
        color_space == "bt470bg"
        and color_transfer in (None, "bt709")
        and color_primaries in (None, "bt470bg")
    ):
        return "sdr-bt601"

    colorimetry_untagged = all(
        value is None
        for value in (color_space, color_transfer, color_primaries)
    )
    if colorimetry_untagged:
        return "sdr-bt709" if color.coded_height >= 720 else "sdr-bt601"
    raise _unsupported_color_error(color)


def build_color_filter(color: SourceColorInfo) -> str:
    """Build the measured, frozen source-to-sRGB conversion filter."""

    rule = color_rule(color)
    color_range = _known_tag(color.color_range)
    if rule in ("sdr-bt709", "sdr-bt601"):
        matrix = "bt709" if rule == "sdr-bt709" else "bt601"
        in_range = "pc" if color_range == "pc" else "tv"
        return (
            f"scale=in_range={in_range}:out_range=full:"
            f"in_color_matrix={matrix}:flags={SWSCALE_FLAGS},format=rgb24"
            f"{SRGB_PARAMS_SUFFIX}"
        )
    if rule == "sdr-bt2020":
        range_in = "full" if color_range == "pc" else "limited"
        return (
            f"zscale=rangein={range_in}:matrixin=2020_ncl:transferin=2020_10:"
            "primariesin=2020:range=full:matrix=709:transfer=709:primaries=709,"
            "format=gbrp16le,scale=flags=accurate_rnd,format=rgb24"
            f"{SRGB_PARAMS_SUFFIX}"
        )
    if rule == "hdr-bt2390":
        in_range = "pc" if color_range == "pc" else "tv"
        return (
            "tonemapx=tonemap=bt2390:t=bt709:m=bt709:p=bt709,"
            f"scale=in_range={in_range}:out_range=full:in_color_matrix=bt709:"
            f"flags={SWSCALE_FLAGS},format=rgb24{SRGB_PARAMS_SUFFIX}"
        )
    raise AssertionError(f"unhandled color rule {rule!r}")


PTS_RE = re.compile(r"\bpts:\s*(-?\d+)")

# Safety ceiling on published frames, not the budget. research/00 caps a film
# near 300; the old ceiling of 32 sat below its own 35-70 episode target, so a
# full-length run at the intended budget was impossible. The budget itself is
# derived below or given per run.
MAX_PUBLISHED_CEILING = 300
# Published frames per DETECTED SHOT, used when --max-candidates is not given.
#
# research/00 states two budgets, 35-70 for an episode and 100-220 for a film,
# as if they were separate rules. They are one rule seen twice: at 0.15 frames
# per detected shot, nine of the ten works it was fitted on land inside their
# own band (episodes at 42-68 frames, films at 114-152). The hidden variable
# is shot count, not duration.
#
# A flat budget is what made the 155-minute feature cover 9% of its shots
# against an episode's 53-85%. And the measured runs say that ceiling is not where
# the content runs out: the duplicate rate holds at 34-48% whether coverage is
# 9% or 85%, so deeper coverage keeps yielding distinct frames rather than
# repeats. 0.15 is fitted to reconcile research's own bands, not derived from
# what a reviewer likes -- expect it to move with more review.
PUBLISH_FRAMES_PER_SHOT = 0.15
# Budget spent on frames a reviewer will delete, chiefly OP, ED and credit
# rolls. Measured at 7% of one 60-frame episode set and 10 of 64 on
# another, so roughly 10% and roughly fixed per work -- which is why it
# hurts more at a derived budget than it did at a flat 200.
#
# This is a deliberate decision not to solve it automatically. The mechanism
# that works (cross-episode repeated-segment matching) needs sibling episodes,
# so films and one-off episodes get nothing from it, and the case that actually
# costs frames -- credits running over the episode underneath -- is its
# documented blind spot. Jellyfin's trickplay tiles do show credit sequences
# legibly at 10s intervals and are a real option, but they live in Jellyfin's
# appdata keyed by Jellyfin's own metadata, which Shoko cannot resolve.
#
# So the reviewer culls, and the budget carries the cost of their doing so.
# Buying back a known 10% loss is cheaper than any of the above and cannot be
# wrong the way an incorrect exclusion interval is wrong: an over-budget run
# shows a reviewer more frames, a bad interval silently deletes real ones.
REVIEW_OVERHEAD = 1.10
BUDGET_FLOOR = 35

# Cost model for _should_batch ONLY: what a per-frame extraction costs in
# decoded video seconds, so `planned * this > duration` means one full decode is
# cheaper. It is a cost coefficient, NOT a seek distance -- measured 10.7-16.5
# across a 37x file-size range, because per-frame cost and sequential decode
# rate both scale with resolution. It was previously named SEEK_PREROLL_SECONDS
# and shared with the seek below, which invited a change that measured a 2x
# regression. Re-derive it empirically if the seek margin changes; never from
# GOP length, which does not enter this ratio at all.
BATCH_COST_SECONDS_PER_FRAME = 10.0
# How far before a target the extractor seeks. ffmpeg's input -ss already lands
# on the preceding keyframe by itself, so this does NOT need to cover a GOP --
# on real sources (median GOP 3.0-4.5 s) the old flat 10.0 decoded two or three
# keyframes of video per frame for nothing, costing ~2x.
#
# What it DOES need to cover is the container start_time: ffmpeg adds that to an
# input -ss, so a margin below it makes the seek overshoot the target, match
# nothing, and decode to EOF. Reproduced on a start_time=7.0 fixture -- at a 6.5
# margin the extraction produced no frame at all. The pipeline's own passage
# slice has start_time 0.083, so zero-start cannot be assumed. Effective margin
# is therefore this plus the measured start_time.
SEEK_MARGIN_SECONDS = 1.0
# Extra shots planned beyond the publish budget, covering shots that publish
# nothing. On real runs 8-12% of shots reaching extraction were rejected by the
# quality floor and productive shots averaged a little over one frame each.
SHOT_PLAN_HEADROOM = 1.25
# How hard to prefer a shot far in time from one already picked. Ranking on
# scene_score alone made the budget a score threshold, which scales with the
# length of the work and left 42 minutes of a 155-minute feature with nothing
# published. Simulated over every measured work: 0.5 removes four of that film's
# five five-minute gaps and every sub-5-second pair, for an 8% drop in mean
# scene score, and 1.0-2.0 buy almost nothing more. 0 restores the old order.
SPREAD_ALPHA = 0.5
# How many frames to extract and MEASURE per published frame.
#
# Ranking decides what to extract, and the only signal it has is scene_score --
# the magnitude of change at the cut. That systematically loses a beautiful shot
# reached by a soft transition from similar material: in one three-minute
# sequence of an episode, 44 of 88 candidates were never extracted, scoring 15-51
# against a threshold near 51, and none of them was a quality-floor reject. Two
# shots the maintainer named as the episode's best were among them.
#
# So extract more than the budget and let the published set be chosen from
# frames that have actually been measured, where a real signal exists: the
# pixel signature already computed for the duplicate check. Cut magnitude picks
# what to look at; visual distinctness picks what to keep.
#
# 3.0 costs roughly a minute on an episode at current extraction speeds. See
# MAX_MEASURED_CEILING below for the feature case, which is where the real
# bound sits -- an earlier estimate here assumed measurement was capped at the
# publish ceiling, which was itself the bug.
#
# Widening also changes which extraction path runs, because `_should_batch`
# sees the widened plan: on a 24-minute episode the plan went 127 -> 383 frames
# and the run flipped from per-frame seeks to one batched decode pass. Measured,
# that was the right call (108 s batched against 134-190 s of per-frame seeks),
# but it means the two constants interact.
# 20 is not a tuning value, it is "measure the whole plan". At any real budget
# this and MAX_MEASURED_CEILING together exceed the candidate pool, so the shot
# plan becomes the only bound and nothing inside a planned shot goes undecoded.
#
# 3.0 left half the pool untouched on the larger works. The surplus is kept now
# rather than deleted, so a frame that is never decoded is the only kind that is
# truly lost. Measured decode cost: 0.22-0.29 s/frame at 1080p and **1.52
# s/frame at 4K**, so covering the 155-minute feature's whole 3,792-candidate
# pool is about 96 minutes of extraction against roughly 25 before. Every other
# measured work already fitted inside the old ceiling, so this changes two
# works of nineteen and costs about 75 minutes across all of them.
EXTRACTION_WIDENING = 20.0
# Ceiling on frames MEASURED in one run, distinct from the publish ceiling.
#
# Reusing MAX_PUBLISHED_CEILING here silently disabled the mechanism exactly
# where it was argued to matter: a 220-frame budget widened only 1.36x and the
# 4K feature's derived budget of 300 widened 1.00x, so the selector returned
# without selecting anything. A ceiling on how many frames may be published is
# not a ceiling on how many may be looked at.
#
# 900 is 3x the publish ceiling, so the policy holds at every budget. It is
# bounded by staging disk rather than time: at 4K a measured frame is about
# 7 MB, so a full pool stages roughly 6 GB in `work/` before the unpublished
# frames are unlinked. Extraction at the measured 0.35-1.5 s a frame puts the
# worst case near 20 minutes, against a 108-minute selection phase on that
# same feature.
# Sized above the largest candidate pool measured -- 3,792, the 4K
# feature -- so it does not silently re-impose the bound EXTRACTION_WIDENING
# just lifted. It remains a runaway guard, and it is bounded by STAGING DISK
# rather than by time: at 4K a measured frame is about 7 MB, so a full pool
# stages roughly 26 GB under `work/` before the published set is bundled and the
# remainder moves to `surplus/`.
MAX_MEASURED_CEILING = 4000
# Temporal spacing applied during FINAL selection, separate from the
# SPREAD_ALPHA that biases which shots get extracted.
#
# They were the same constant, applied at two stages with different ideal
# intervals (duration/shot_budget when ranking, duration/budget when
# selecting), so the second could not be tuned or disabled without moving
# the first. A coupled objective nobody chose is not a policy. Adversarial
# review flagged this against the measured regression where the longest
# published gap grew from 96 s to 127 s.
SELECT_SPACING_ALPHA = 0.5
# ffmpeg's expression parser rejects a `select` built from hundreds of eq()
# terms: 486 failed outright on a real episode after 268 s of work. Chunking
# keeps each expression small; chunks are contiguous in PTS so each pass seeks
# to its own span rather than re-reading the file.
BATCH_CHUNK_SIZE = 48

def _run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    # ffmpeg writes stream metadata into stderr verbatim, and real sources carry
    # bytes that are not decodable in the host's default locale -- one 4K HEVC
    # BDRip crashed subprocess under cp1252. Decoding
    # errors must never fail an extraction that otherwise succeeded.
    if kwargs.get("text") and "errors" not in kwargs:
        kwargs.setdefault("encoding", "utf-8")
        kwargs["errors"] = "replace"
    return subprocess.run(command, check=True, **kwargs)


def _container_start_time(video_path: str, ffprobe_path: str) -> float:
    """The container's own start timestamp, which an input -ss is added to.

    Read from the video stream rather than the format, because the extraction
    seeks and selects in that stream's timeline. Absent or unparseable means
    zero, which is the common case and the safe assumption for a margin.
    """
    result = _run(
        [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=start_time",
            "-of", "json",
            "--", video_path,
        ],
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        return 0.0
    try:
        return max(0.0, float(streams[0].get("start_time")))
    except (TypeError, ValueError):
        return 0.0


def _tool_build(tool: str) -> str:
    result = _run([tool, "-hide_banner", "-version"], capture_output=True, text=True)
    return result.stdout.splitlines()[0]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()


def _load_object(path: str, label: str) -> dict:
    with open(path, encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _probe_stream(video_path: str, ffprobe_path: str) -> tuple[int, str]:
    result = _run(
        [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=index,time_base",
            "-of", "json",
            "--", video_path,
        ],
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if len(streams) != 1:
        raise RuntimeError("source must have a first video stream")
    return int(streams[0]["index"]), streams[0]["time_base"]


#: How far before its stated length the picture may end in an ordinary file.
#: The sampler plans the last shot to the container's duration, which is
#: not the video's: audio or subtitles commonly run a second or two past the
#: last frame, and a Matroska segment can declare the end of its last chapter
#: (seen: 9.7 s past every track). So a sample can land after the last frame
#: (seen: 1.3 s). The stated length measured against is the video track's own
#: where the file gives one (`_video_stated_seconds`), so neither of those
#: counts against the picture; only a file that does not say falls back to
#: the container's duration. A picture that ends further back than this
#: stopped early -- the demuxer lost the stream at damaged or missing bytes
#: (seen: unreadable for the last 37% of an episode) -- and scene detection
#: does not notice, because it reads up to the same point and still finds
#: plenty of cuts.
PICTURE_END_TOLERANCE_SECONDS = 5.0


def _last_frame_seconds(video_path: str, time_base: str, ffprobe_path: str) -> float:
    """Where the readable picture ends: the largest video packet PTS.

    One packet pass over the file (about a second for an episode, see
    `_resolve_pts_bulk`), run only when a planned timestamp found no frame.
    """
    result = _run(
        [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "packet=pts",
            "-of", "json",
            "--", video_path,
        ],
        capture_output=True,
        text=True,
    )
    pts_values = [
        int(packet["pts"])
        for packet in json.loads(result.stdout).get("packets", [])
        if packet.get("pts") is not None
    ]
    if not pts_values:
        raise RuntimeError("source reported no frame timestamps")
    return float(max(pts_values) * Fraction(time_base))


def _video_stated_seconds(video_path: str, ffprobe_path: str) -> Optional[float]:
    """How long the video track says it runs, or None if it does not say.

    The picture-end check measures against this rather than the container's
    duration. A Matroska segment's duration is whatever the muxer wrote, and
    releases write the end of their last chapter there (seen: 9.7 s past
    every track, so a whole episode was refused). The video track's own
    length is what a picture that stops early falls short of: a file that
    loses its picture at damaged bytes still declares the full track.
    """
    result = _run(
        [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=duration:stream_tags=DURATION",
            "-of", "json",
            "--", video_path,
        ],
        capture_output=True,
        text=True,
    )
    streams = json.loads(result.stdout).get("streams", [])
    if not streams:
        return None
    stream = streams[0]
    try:
        seconds = float(stream["duration"])
        if seconds > 0:
            return seconds
    except (KeyError, TypeError, ValueError):
        pass
    # Matroska keeps a track's length only as a tag, e.g. 00:23:40.002000000.
    tag = (stream.get("tags") or {}).get("DURATION", "")
    match = re.fullmatch(r"(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", tag.strip())
    if not match:
        return None
    hours, minutes, secs = match.groups()
    seconds = int(hours) * 3600 + int(minutes) * 60 + float(secs)
    return seconds if seconds > 0 else None


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


def _refuse_if_picture_stops_early(picture_end: float, stated_end: float,
                                   stated_by: str = "the file") -> None:
    """Refuse a file whose picture ends well before its stated length.

    `stated_end` is the video track's own length where the file gives one
    (`_video_stated_seconds`), else the container's; `stated_by` names which,
    for the message. Worded as a refusal so the worker ends the stage as
    ineligible on the first attempt: the same bytes fail the same way every
    time.
    """
    if stated_end <= 0 or stated_end - picture_end > PICTURE_END_TOLERANCE_SECONDS:
        raise RuntimeError(
            f"refused: the picture stops at {_clock(picture_end)} "
            f"({picture_end:.3f}s) but {stated_by} says it runs "
            f"{_clock(stated_end)} ({stated_end:.3f}s); the file "
            f"looks damaged or cut short. Replace it, then onboard it again."
        )


def _past_picture_end(candidate: CandidateRecord, picture_end: float) -> dict:
    """Audit record for a sample planned after the last frame."""
    return {
        "reason": "past_the_last_frame",
        "selected_candidate_id": "",
        "framing_distance": 0.0,
        "threshold": 0.0,
        "picture_end_seconds": round(picture_end, 6),
        "candidate": candidate.to_dict(),
    }


def _resolve_pts(video_path: str, timestamp: float, time_base: str, ffprobe_path: str) -> Optional[int]:
    """Resolve one timestamp to the exact PTS at or after it, or None when
    no frame follows it (the caller decides whether that is the file's
    ordinary tail or a damaged file).

    Reads packets rather than frames: `-show_entries frame=pts` decodes the
    600-frame window to report frame properties, measured at 2850 ms a lookup
    against 74 ms for packets on 1080p HEVC. This runs once per candidate on
    the per-frame extraction path, so on a 364-candidate run it was ~17 minutes
    spent decoding purely to read timestamps. Verified to return identical
    answers on 14 timestamps across real HEVC and h264 sources.
    """
    target_pts = math.ceil(Fraction(str(timestamp)) / Fraction(time_base))
    result = _run(
        [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-read_intervals", f"{timestamp:.6f}%+#600",
            "-show_entries", "packet=pts",
            "-of", "json",
            "--", video_path,
        ],
        capture_output=True,
        text=True,
    )
    pts_values = [
        int(packet["pts"])
        for packet in json.loads(result.stdout).get("packets", [])
        if packet.get("pts") is not None and int(packet["pts"]) >= target_pts
    ]
    if not pts_values:
        return None
    return min(pts_values)


def _resolve_pts_bulk(
    video_path: str,
    timestamps: list[float],
    time_base: str,
    ffprobe_path: str,
) -> list[Optional[int]]:
    """Resolve many timestamps to exact PTS in one metadata pass.

    `_resolve_pts` spawns an ffprobe per timestamp, each reading up to 600
    frames of packet metadata. At a few candidates that is nothing; at several
    hundred it is minutes of silence before any frame is extracted, which is
    what made a full-episode run look hung. One pass answers every timestamp.

    That pass reads PACKETS, not frames. `-show_entries frame=pts` decodes the
    whole file to report frame properties: measured on a real 24-minute 1080p
    HEVC episode it took 168 s against 1 s for packets, so a batched run was
    decoding the source twice. For a video stream one packet is one frame, and
    both were verified to yield identical sorted PTS sets on real HEVC (34,526)
    and h264 (38,241) sources. Packets arrive in storage order under B-frame
    reordering, which does not matter -- the result is sorted for bisect below.
    """
    if not timestamps:
        return []
    result = _run(
        [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "packet=pts",
            "-of", "json",
            "--", video_path,
        ],
        capture_output=True,
        text=True,
    )
    available = sorted(
        int(packet["pts"])
        for packet in json.loads(result.stdout).get("packets", [])
        if packet.get("pts") is not None
    )
    if not available:
        raise RuntimeError("source reported no frame timestamps")

    # None marks a timestamp with no frame at or after it; the caller decides
    # whether that is the file's ordinary tail or a damaged file.
    resolved: list[Optional[int]] = []
    for timestamp in timestamps:
        target = math.ceil(Fraction(str(timestamp)) / Fraction(time_base))
        index = bisect.bisect_left(available, target)
        resolved.append(available[index] if index < len(available) else None)
    return resolved


def _extract_batch(
    video_path: str,
    wanted: list[tuple[str, int]],
    stage: Path,
    ffmpeg_path: str,
    color_filter: str,
    time_base: str = "1/1000",
    seek_preroll: float = SEEK_MARGIN_SECONDS,
) -> dict[str, int]:
    """Extract many exact-PTS frames in one decode pass.

    The per-frame path spawns an ffmpeg process per candidate, each seeking and
    decoding forward to a single target. Measured on 1080p 10-bit HEVC with a
    10-second GOP that costs 303 ms a frame against 83 ms here, and accuracy
    survives: both were exact-PTS correct on every target.

    `wanted` is (candidate_id, pts) and MUST be sorted by pts, because `select`
    emits in decode order and the image2 pattern numbers sequentially -- the
    mapping from output file back to candidate is positional. That is a sharp
    edge, so the observed PTS sequence is checked against the requested one and
    a mismatch raises rather than silently mislabelling frames.
    """
    if not wanted:
        return {}
    if [pts for _, pts in wanted] != sorted(pts for _, pts in wanted):
        raise RuntimeError("batch extraction requires candidates sorted by pts")

    extracted: dict[str, int] = {}
    chunks = [wanted[i:i + BATCH_CHUNK_SIZE]
              for i in range(0, len(wanted), BATCH_CHUNK_SIZE)]
    for number, chunk in enumerate(chunks, start=1):
        _extract_one_batch(video_path, chunk, stage, ffmpeg_path, time_base,
                           color_filter, seek_preroll)
        extracted.update(dict(chunk))
        print(f"[extract] chunk {number}/{len(chunks)}: "
              f"{len(extracted)}/{len(wanted)} frames", flush=True)
    return extracted


def _extract_one_batch(
    video_path: str,
    wanted: list[tuple[str, int]],
    stage: Path,
    ffmpeg_path: str,
    time_base: str,
    color_filter: str,
    seek_preroll: float = SEEK_MARGIN_SECONDS,
) -> None:
    """One decode pass over the span a single chunk covers."""
    pts_list = [pts for _, pts in wanted]
    # Seek to just before the chunk's first target. Chunks are contiguous in
    # PTS, so this keeps each pass to its own span rather than re-reading the
    # whole file per chunk. The margin is paid once per chunk, not per frame,
    # so it matters far less here than on the per-frame path.
    seek = max(0.0, float(Fraction(pts_list[0]) * Fraction(time_base))
               - seek_preroll)
    expression = "+".join(f"eq(pts\\,{pts})" for pts in pts_list)
    pattern = stage / ".batch-%05d.png"
    result = _run(
        [
            ffmpeg_path,
            "-nostdin", "-hide_banner", "-v", "info", "-copyts",
            "-ss", f"{seek:.6f}",
            "-i", video_path,
            "-map", "0:v:0",
            "-vf", f"select='{expression}',{color_filter},showinfo",
            "-fps_mode", "passthrough",
            "-frames:v", str(len(pts_list)),
            "-f", "image2",
            "-c:v", "png", *PNG_ENCODER_OPTIONS,
            "--", str(pattern),
        ],
        capture_output=True,
        text=True,
    )
    observed = [int(value) for value in PTS_RE.findall(result.stderr)]
    if observed != pts_list:
        raise RuntimeError(
            f"batch chunk emitted {len(observed)} frames for {len(pts_list)} "
            "targets and the PTS sequences differ; refusing to map outputs to "
            "candidates positionally"
        )
    for index, (candidate_id, _pts) in enumerate(wanted, start=1):
        produced = stage / f".batch-{index:05d}.png"
        if not produced.is_file() or produced.stat().st_size == 0:
            raise RuntimeError(f"batch chunk produced no file for {candidate_id}")
        produced.rename(stage / f"{candidate_id}.png")


def _extract_exact_frame(
    video_path: str,
    timestamp: float,
    intended_pts: int,
    output_path: Path,
    ffmpeg_path: str,
    color_filter: str,
    seek_preroll: float = SEEK_MARGIN_SECONDS,
) -> int:
    seek = max(0.0, timestamp - seek_preroll)
    # Bound the read. If the seek overshoots, select=eq(pts,N) matches nothing
    # and ffmpeg decodes to end of file -- measured at 1m50s on the 4K feature
    # with 1290 s remaining, and ~13 minutes near its head. That reads as a hang
    # rather than the error it is. Reading a few seconds past the target turns
    # the same failure into a fast, truthful one.
    read_until = timestamp + seek_preroll + SEEK_MARGIN_SECONDS
    result = _run(
        [
            ffmpeg_path,
            "-nostdin", "-hide_banner", "-v", "info", "-copyts",
            "-ss", f"{seek:.6f}",
            "-to", f"{read_until:.6f}",
            "-i", video_path,
            "-map", "0:v:0",
            "-vf", f"select=eq(pts\\,{intended_pts}),{color_filter},showinfo",
            "-fps_mode", "passthrough",
            "-frames:v", "1",
            "-f", "image2",
            "-update", "1",
            "-c:v", "png", *PNG_ENCODER_OPTIONS,
            "--", str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    matches = PTS_RE.findall(result.stderr)
    if not matches or not output_path.is_file() or output_path.stat().st_size == 0:
        raise RuntimeError(f"exact extraction produced no frame at PTS {intended_pts}")
    observed_pts = int(matches[0])
    if observed_pts != intended_pts:
        raise RuntimeError(f"extracted PTS {observed_pts} does not match {intended_pts}")
    return observed_pts


def _image_dimensions(path: Path, ffprobe_path: str) -> tuple[int, int]:
    result = _run(
        [
            ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            "--", str(path),
        ],
        capture_output=True,
        text=True,
    )
    width, height = result.stdout.strip().split(",")
    return int(width), int(height)


def _bundle_digest(manifest_sha: str) -> str:
    """One short name for a bundle's exact contents, recorded with every
    result made from it. Derived from the list's checksum and nothing else."""
    return hashlib.sha256(
        f"hikari-candidate-bundle\n{manifest_sha}\n".encode()
    ).hexdigest()


def _pipeline_digest() -> str:
    return _sha256(Path(__file__))


def _partition_candidates(candidates: list[CandidateRecord]) -> tuple[
    list[CandidateRecord], list[CandidateRecord]
]:
    held = [candidate for candidate in candidates if candidate.exclusion_status == "held_unresolved"]
    cleared = [candidate for candidate in candidates if candidate.exclusion_status == "cleared"]
    return cleared, held


def _select_by_distinctness(
    retained: list[CandidateRecord],
    signatures: dict[str, Any],
    max_candidates: int,
    source_duration: float,
    spread_alpha: float,
) -> tuple[list[CandidateRecord], list[CandidateRecord]]:
    """Choose the published set from measured frames, by how different they are.

    Mirrors `_rank_shots`: greedy, each step taking the candidate whose value is
    highest, where value is its distance from everything already chosen scaled
    by the same temporal-spacing factor. The difference is the signal. Ranking
    could only ask how big the cut was; here every frame has been decoded and
    measured, so it can ask how different the image actually is.

    Returns the kept set, the measured-but-not-published remainder, and for
    each of those the frame that displaced it with the distance measured.
    """
    if len(retained) <= max_candidates:
        return retained, [], {}
    ideal = source_duration / max_candidates if source_duration > 0 else 0.0
    pool = sorted(retained, key=lambda c: (-c.scene_score, c.timestamp_seconds))
    chosen: list[CandidateRecord] = [pool.pop(0)]
    # Each candidate's nearest distance to the chosen set only ever shrinks, and
    # only because of the frame just added, so it is carried forward rather than
    # recomputed. Recomputing every prior distance each round made this O(N*K^2)
    # -- about 13 million signature comparisons at a 300-frame budget over a
    # 900-frame pool, each allocating NumPy intermediates. This is O(N*K).
    nearest_image = [
        frame_quality.framing_distance(
            signatures[candidate.candidate_id], signatures[chosen[0].candidate_id])
        for candidate in pool
    ]
    # Which frame that distance is to. Carried purely so an omitted frame can
    # name what displaced it; a rejection a reviewer cannot reconstruct is not
    # evidence.
    nearest_id = [chosen[0].candidate_id for _ in pool]
    nearest_time = [
        abs(candidate.timestamp_seconds - chosen[0].timestamp_seconds)
        for candidate in pool
    ]
    while pool and len(chosen) < max_candidates:
        best_index, best_value = 0, -1.0
        for index in range(len(pool)):
            value = nearest_image[index]
            if ideal > 0 and spread_alpha > 0:
                value *= min(1.0, nearest_time[index] / ideal) ** spread_alpha
            if value > best_value:
                best_index, best_value = index, value
        picked = pool.pop(best_index)
        nearest_image.pop(best_index)
        nearest_id.pop(best_index)
        nearest_time.pop(best_index)
        chosen.append(picked)
        for index, candidate in enumerate(pool):
            distance = frame_quality.framing_distance(
                signatures[candidate.candidate_id], signatures[picked.candidate_id])
            if distance < nearest_image[index]:
                nearest_image[index] = distance
                nearest_id[index] = picked.candidate_id
            gap = abs(candidate.timestamp_seconds - picked.timestamp_seconds)
            if gap < nearest_time[index]:
                nearest_time[index] = gap
    displaced_by = {
        candidate.candidate_id: (nearest_id[index], nearest_image[index])
        for index, candidate in enumerate(pool)
    }
    return chosen, pool, displaced_by


def _rank_shots(
    cleared: list[CandidateRecord],
    source_duration: float = 0.0,
    shot_budget: int = 0,
    spread_alpha: float = SPREAD_ALPHA,
) -> list[tuple[str, list[CandidateRecord]]]:
    """Group cleared candidates by shot and rank the shots, not the frames.

    scene_score is a property of the cut that opened a shot, so every sample
    inside one shot carries the same value. Ranking frames by it therefore put
    a shot's samples adjacent at the top of the order and published them
    back-to-back as near-twins. Ranking shots makes that impossible.

    Ranking by score ALONE, however, turns the budget into a score threshold,
    and the threshold rises with the length of the work: measured at 51 on a
    25-minute episode and 92 on a 155-minute feature. Any stretch whose cuts
    fall below it publishes nothing however long it runs, which left four gaps
    totalling 42 minutes of that feature dark, including a quiet dialogue
    sequence -- exactly the material that scores low.

    So a shot sitting closer in time than the even spacing the budget implies
    is discounted. A high-scoring cut still wins; the fifth high-scoring cut
    inside ten seconds no longer beats the only cut in a quiet three minutes.
    `spread_alpha=0` restores pure score order exactly.
    """
    shots: dict[str, list[CandidateRecord]] = {}
    for candidate in cleared:
        shots.setdefault(candidate.shot_id, []).append(candidate)
    for members in shots.values():
        members.sort(key=lambda candidate: candidate.timestamp_seconds)
    by_score = sorted(
        shots.items(),
        key=lambda item: (-item[1][0].scene_score, item[1][0].timestamp_seconds),
    )
    if spread_alpha <= 0 or source_duration <= 0 or shot_budget <= 0:
        return by_score

    ideal = source_duration / shot_budget
    if ideal <= 0:
        return by_score

    remaining = list(by_score)
    taken: list[float] = []          # timestamps of picks, kept sorted for bisect
    ordered: list[tuple[str, list[CandidateRecord]]] = []
    while remaining:
        best_index, best_value = 0, -1.0
        for index, item in enumerate(remaining):
            at = item[1][0].timestamp_seconds
            if taken:
                pos = bisect.bisect_left(taken, at)
                nearest = min(
                    (at - taken[pos - 1]) if pos > 0 else math.inf,
                    (taken[pos] - at) if pos < len(taken) else math.inf,
                )
                factor = min(1.0, nearest / ideal) ** spread_alpha
            else:
                factor = 1.0
            value = item[1][0].scene_score * factor
            if value > best_value:
                best_index, best_value = index, value
        chosen = remaining.pop(best_index)
        bisect.insort(taken, chosen[1][0].timestamp_seconds)
        ordered.append(chosen)
    return ordered


def _plan_extractions(
    ranked_shots: list[tuple[str, list[CandidateRecord]]],
    max_candidates: int,
    max_extractions: int,
) -> tuple[list[CandidateRecord], list[CandidateRecord], str]:
    """Shots in rank order, only as many as the publish budget can consume.

    Bounding the plan by `max_extractions` instead was a regression: with the
    default budget of 8 published frames it would have extracted up to 800
    candidates to choose from, and on a real episode it extracted all 631 to
    publish 200. The publishing loop stops at `max_candidates`, so everything
    past that point is decoded and thrown away.

    Headroom is for shots that publish nothing. On real runs 8-12% of the shots
    reaching extraction were rejected outright by the quality floor, and the
    productive ones averaged a little over one published frame each, so a
    quarter more shots than the budget covers it.

    Also reports which bound actually stopped the plan. Both outcomes used to be
    recorded as `extraction_attempt_limit`, which was true only by accident:
    hitting the shot budget is the run working as designed, while hitting
    `max_extractions` means the run was cut short of its own measurement target
    and its results are a truncation rather than a selection. On one long
    film the two were indistinguishable in the audit and the truncation went
    unnoticed.
    """
    shot_budget = math.ceil(max_candidates * SHOT_PLAN_HEADROOM)
    planned: list[CandidateRecord] = []
    deferred: list[CandidateRecord] = []
    stopped_by = ""
    for index, (_shot_id, members) in enumerate(ranked_shots):
        within_budget = index < shot_budget
        within_attempts = len(planned) + len(members) <= max_extractions
        if within_budget and within_attempts:
            planned.extend(members)
            continue
        if not stopped_by:
            # Attempts first: if both bind, the run is still truncated.
            stopped_by = ("extraction_attempt_limit" if not within_attempts
                          else "shot_plan_budget")
        deferred.extend(members)
    return planned, deferred, stopped_by


def _should_batch(planned: int, source_duration: float) -> bool:
    """Batch only when one full decode beats seeking to each frame.

    A per-frame extraction seeks to a keyframe and decodes forward, costing
    roughly the pre-roll in video seconds. A batch decodes the file once. So
    batching wins once the planned frames would together decode more of the
    source than the source contains -- and loses badly below that, because a
    handful of frames scattered by rank would otherwise drag a whole episode
    through the decoder.
    """
    if source_duration <= 0:
        return False
    return planned * BATCH_COST_SECONDS_PER_FRAME > source_duration


def _publish_best_in_shot(
    cleared: list[CandidateRecord],
    input_path: str,
    time_base: str,
    stream_index: int,
    stage: Path,
    ffmpeg_path: str,
    ffprobe_path: str,
    color_filter: str,
    max_candidates: int,
    max_extractions: int,
    min_detail: float,
    min_entropy: float,
    intra_shot_distinct: float,
    cross_shot_duplicate: float,
    max_per_shot: int = 2,
    dark_luma: float = frame_quality.DEFAULT_DARK_LUMA,
    bright_luma: float = frame_quality.DEFAULT_BRIGHT_LUMA,
    blank_luma_stddev: float = frame_quality.DEFAULT_BLANK_LUMA_STDDEV,
    source_duration: float = 0.0,
    surplus: Optional[Path] = None,
    spread_alpha: float = SPREAD_ALPHA,
    select_spacing_alpha: float = SELECT_SPACING_ALPHA,
) -> dict:
    """Publish one representative per shot, plus a second only if it is distinct.

    Every sample in a shot is extracted and measured, the best-quality one is
    published, and the rest are kept only when their framing genuinely differs
    from the published frame -- a pan arriving somewhere new or an effect
    entering, not a mouth flap.
    """
    from . import frame_quality

    retained: list[CandidateRecord] = []
    extracted_candidates: dict[str, dict] = {}
    measured: dict[str, dict] = {}
    published_signatures: list[tuple[str, Any]] = []
    quality_omissions: list[dict] = []
    redundant_omissions: list[dict] = []
    unprocessed: list[CandidateRecord] = []
    capacity_omitted: list[CandidateRecord] = []
    shot_audit: list[dict] = []
    extraction_attempts = 0
    stop_reason: Optional[str] = None

    def record(candidate: CandidateRecord, artifact_name: str, artifact_path: Path,
               intended_pts: int, observed_pts: int, width: int, height: int,
               quality: "frame_quality.FrameQuality",
               disposition: str, disposition_reason: str,
               geometry: "composition.CompositionGeometry") -> None:
        extracted_candidates[candidate.candidate_id] = {
            "candidate_id": candidate.candidate_id,
            "shot_id": candidate.shot_id,
            "stream_index": stream_index,
            "intended_pts": intended_pts,
            "observed_pts": observed_pts,
            "time_base": time_base,
            "artifact_name": artifact_name,
            "sha256": _sha256(artifact_path),
            "bytes": artifact_path.stat().st_size,
            "width": width,
            "height": height,
            "encoding": "png",
            "pixel_format": "rgb24",
            "color_intent": "sRGB SDR full-range",
            "scene_score": candidate.scene_score,
            "exclusion_status": "cleared",
        }
        # Quality stays in the audit trail, not the bundle's list: it is
        # review evidence, and the analyze worker has no use for it. The
        # disposition rides with it for the same reason -- a `review` frame
        # is published normally and is only distinguishable here.
        entry = quality.to_dict()
        entry.update(geometry.to_dict())
        entry["floor_disposition"] = disposition
        if disposition_reason:
            entry["floor_reason"] = disposition_reason
        measured[candidate.candidate_id] = entry

    # Measure a multiple of the budget so the published set can be chosen on a
    # real signal rather than on the cut score of shots never looked at.
    measure_budget = min(
        MAX_MEASURED_CEILING,
        math.ceil(max_candidates * EXTRACTION_WIDENING),
    )
    ranked_shots = _rank_shots(
        cleared, source_duration,
        math.ceil(measure_budget * SHOT_PLAN_HEADROOM), spread_alpha,
    )
    planned, _beyond_plan, plan_stopped_by = _plan_extractions(
        ranked_shots, measure_budget, max_extractions
    )
    if plan_stopped_by == "extraction_attempt_limit":
        print(f"[extract] WARNING: --max-extractions {max_extractions} cut the "
              f"plan short of its {measure_budget}-frame measurement target. "
              f"This run is a truncation, not a selection; raise the cap and "
              f"re-run before drawing conclusions from it.", flush=True)
    planned_ids = {candidate.candidate_id for candidate in planned}
    batching = _should_batch(len(planned), source_duration)
    # One metadata read, not a packet scan: the margin only has to clear the
    # container's start_time, and ffmpeg finds the keyframe by itself.
    start_time = _container_start_time(input_path, ffprobe_path)
    seek_preroll = start_time + SEEK_MARGIN_SECONDS
    print(f"[extract] {len(planned)} candidates planned from "
          f"{len(ranked_shots)} shots; "
          f"{'one batched decode pass' if batching else 'per-frame seeks'}; "
          f"seek margin {seek_preroll:.3f}s (start_time {start_time:.3f}s)",
          flush=True)

    pts_by_candidate: dict[str, int] = {}
    extracted_ids: set[str] = set()
    # Samples planned after the last frame (see PICTURE_END_TOLERANCE_SECONDS).
    past_end_ids: set[str] = set()
    picture_end: Optional[float] = None

    def note_past_end(candidate: CandidateRecord) -> None:
        nonlocal picture_end
        if picture_end is None:
            picture_end = _last_frame_seconds(input_path, time_base, ffprobe_path)
            track_end = _video_stated_seconds(input_path, ffprobe_path)
            if track_end is None:
                _refuse_if_picture_stops_early(picture_end, source_duration)
            else:
                _refuse_if_picture_stops_early(picture_end, track_end, "its video track")
            print(f"[extract] the picture ends at {picture_end:.3f}s, "
                  f"{source_duration - picture_end:.3f}s before the file's "
                  f"stated end; samples after it are dropped", flush=True)
        past_end_ids.add(candidate.candidate_id)
        redundant_omissions.append(_past_picture_end(candidate, picture_end))

    if batching:
        # One metadata pass for every timestamp. Per-candidate ffprobe calls
        # were minutes of silence before extraction began on a full episode.
        print(f"[extract] resolving {len(planned)} timestamps in one pass",
              flush=True)
        for candidate, pts in zip(planned, _resolve_pts_bulk(
            input_path,
            [c.timestamp_seconds for c in planned],
            time_base,
            ffprobe_path,
        )):
            if pts is None:
                note_past_end(candidate)
                continue
            pts_by_candidate[candidate.candidate_id] = pts
        # Two samples can land on one frame in a short shot. The batch maps
        # outputs positionally, so a repeated pts would desynchronise it.
        batch: list[tuple[str, int]] = []
        seen_pts: set[int] = set()
        for candidate_id, pts in sorted(
            pts_by_candidate.items(), key=lambda item: item[1]
        ):
            if pts in seen_pts:
                continue
            seen_pts.add(pts)
            batch.append((candidate_id, pts))
        _extract_batch(
            input_path, batch, stage, ffmpeg_path, color_filter, time_base,
            seek_preroll,
        )
        extracted_ids = {candidate_id for candidate_id, _ in batch}
        print(f"[extract] batch produced {len(extracted_ids)} frames", flush=True)
        extraction_attempts = len(extracted_ids)

    extract_started = time.monotonic()
    next_progress_at = extract_started + PROGRESS_INTERVAL_SECONDS

    for shot_id, members in ranked_shots:
        if stop_reason is not None:
            if stop_reason == "outside_the_extraction_plan":
                unprocessed.extend(members)
            else:
                capacity_omitted.extend(members)
            continue
        if len(retained) >= measure_budget:
            stop_reason = "prepared_bundle_candidate_limit"
            capacity_omitted.extend(members)
            continue

        # Extract and measure every sample in this shot so the best can be chosen.
        surviving: list[tuple[CandidateRecord, dict]] = []
        deferred: list[CandidateRecord] = []
        for candidate in members:
            if candidate.candidate_id not in planned_ids:
                stop_reason = "outside_the_extraction_plan"
                deferred.append(candidate)
                continue
            artifact_name = f"{candidate.candidate_id}.png"
            artifact_path = stage / artifact_name
            if candidate.candidate_id in past_end_ids:
                continue  # recorded when it failed to resolve
            if batching:
                if candidate.candidate_id not in extracted_ids:
                    # Its pts collided with an earlier sibling's, so it is
                    # literally the same frame rather than a near-twin.
                    redundant_omissions.append({
                        "reason": "same_frame_as_sibling",
                        "selected_candidate_id": "",
                        "framing_distance": 0.0,
                        "threshold": 0.0,
                        "candidate": candidate.to_dict(),
                    })
                    continue
                # _extract_batch verified the emitted PTS sequence against the
                # requested one before mapping files to candidates, so an exact
                # match is already established here.
                intended_pts = pts_by_candidate[candidate.candidate_id]
                observed_pts = intended_pts
            else:
                intended_pts = _resolve_pts(
                    input_path, candidate.timestamp_seconds, time_base, ffprobe_path
                )
                if intended_pts is None:
                    note_past_end(candidate)
                    continue
                observed_pts = _extract_exact_frame(
                    input_path, candidate.timestamp_seconds, intended_pts,
                    artifact_path, ffmpeg_path, color_filter, seek_preroll,
                )
                extraction_attempts += 1
                # The batched path reports per chunk; this one reported nothing
                # until the whole phase ended, which on a feature is ten-plus
                # minutes of silence that reads as a hang.
                now = time.monotonic()
                if now >= next_progress_at:
                    next_progress_at = now + PROGRESS_INTERVAL_SECONDS
                    rate = extraction_attempts / max(1e-9, now - extract_started)
                    left = (len(planned) - extraction_attempts) / rate
                    print(f"[extract] {extraction_attempts}/{len(planned)} "
                          f"attempts, {len(retained)} published, "
                          f"{rate:.1f}/s, ~{left / 60:.0f} min left", flush=True)
            width, height = _image_dimensions(artifact_path, ffprobe_path)
            png_bytes = artifact_path.read_bytes()
            analysis = frame_quality.analyze(png_bytes)
            # Cheap deterministic geometry, measured here because it needs no
            # model. The composition VERDICT needs the tagger and face boxes and
            # is assembled in providers/annotation.
            geometry = composition.measure(png_bytes)

            disposition, disposition_reason = frame_quality.floor_disposition(
                analysis.quality, min_detail, min_entropy, dark_luma,
                bright_luma, blank_luma_stddev,
            )
            if disposition == frame_quality.BLANK:
                artifact_path.unlink()
                quality_omissions.append({
                    "reason": disposition_reason,
                    "disposition": disposition,
                    "frame_quality": analysis.quality.to_dict(),
                    "candidate": candidate.to_dict(),
                })
                continue
            # A `review` frame competes for the budget on the same terms as any
            # other. Its low detail already ranks it last inside its own shot,
            # so it wins only where the shot holds nothing better -- which is
            # exactly the case the old floor answered by deleting the shot.
            surviving.append((candidate, {
                "analysis": analysis,
                "geometry": geometry,
                "disposition": disposition,
                "disposition_reason": disposition_reason,
                "artifact_name": artifact_name,
                "artifact_path": artifact_path,
                "intended_pts": intended_pts,
                "observed_pts": observed_pts,
                "width": width,
                "height": height,
            }))

        unprocessed.extend(deferred)
        if not surviving:
            shot_audit.append({
                "shot_id": shot_id,
                "sampled": len(members),
                "published": 0,
                # Before the disposition split this fired whenever every sample
                # was luma-extreme and featureless, which cost one film 25
                # shots and nine frames worth keeping. Now only an actual blank
                # counts.
                "outcome": "every_sample_was_blank",
            })
            continue

        midpoint = (members[0].timestamp_seconds + members[-1].timestamp_seconds) / 2.0
        ordered = sorted(
            surviving,
            key=lambda item: (
                -item[1]["analysis"].rank_key,
                abs(item[0].timestamp_seconds - midpoint),
                item[0].timestamp_seconds,
            ),
        )

        shot_published = 0
        for candidate, data in ordered:
            analysis = data["analysis"]
            artifact_path = data["artifact_path"]
            if shot_published >= max_per_shot:
                artifact_path.unlink()
                redundant_omissions.append({
                    "reason": "shot_publish_limit",
                    "selected_candidate_id": retained[-1].candidate_id,
                    "threshold": max_per_shot,
                    "candidate": candidate.to_dict(),
                })
                continue
            if len(retained) >= measure_budget:
                stop_reason = "prepared_bundle_candidate_limit"
                artifact_path.unlink()
                capacity_omitted.append(candidate)
                continue

            # The first frame kept from a shot only has to be distinct from
            # other shots; a second has to clear the higher intra-shot bar.
            floor_distance = intra_shot_distinct if shot_published else cross_shot_duplicate
            nearest_id, nearest = "", 1.0
            for published_id, published_signature in published_signatures:
                distance = frame_quality.framing_distance(
                    analysis.signature, published_signature
                )
                if distance < nearest:
                    nearest_id, nearest = published_id, distance
            if published_signatures and nearest < floor_distance:
                artifact_path.unlink()
                redundant_omissions.append({
                    "reason": "same_shot_near_twin" if shot_published else "repeated_framing",
                    "selected_candidate_id": nearest_id,
                    "framing_distance": round(nearest, 6),
                    "threshold": floor_distance,
                    "candidate": candidate.to_dict(),
                })
                continue

            retained.append(candidate)
            published_signatures.append((candidate.candidate_id, analysis.signature))
            record(candidate, data["artifact_name"], artifact_path,
                   data["intended_pts"], data["observed_pts"],
                   data["width"], data["height"], analysis.quality,
                   data["disposition"], data["disposition_reason"],
                   data["geometry"])
            shot_published += 1

        shot_audit.append({
            "shot_id": shot_id,
            "sampled": len(members),
            "published": shot_published,
            "outcome": "published" if shot_published else "all_samples_redundant",
        })

    kept, breadth_omitted, displaced_by = _select_by_distinctness(
        retained, dict(published_signatures), max_candidates,
        source_duration, select_spacing_alpha,
    )
    breadth_measured: dict[str, dict] = {}
    if breadth_omitted:
        print(f"[extract] measured {len(retained)}, publishing the {len(kept)} "
              f"most distinct", flush=True)
        keep_ids = {candidate.candidate_id for candidate in kept}
        signature_of = dict(published_signatures)
        for candidate in breadth_omitted:
            entry = extracted_candidates.pop(candidate.candidate_id, None)
            # The pixels go, the measurement stays. Dropping it silently lost
            # the only record that a frame had been questioned at all, so a
            # review frame could be extracted, dispositioned, and discarded
            # leaving nothing to reconstruct the decision from.
            quality = measured.pop(candidate.candidate_id, None)
            nearest_id, nearest_distance = displaced_by.get(
                candidate.candidate_id, ("", 1.0))
            breadth_measured[candidate.candidate_id] = {
                "frame_quality": quality,
                "displaced_by_candidate_id": nearest_id,
                "framing_distance": round(nearest_distance, 6),
            }
            if entry is not None:
                staged = stage / Path(entry["artifact_name"]).name
                if staged.is_file():
                    # Decoded, measured, cleared the floor, and beaten only by
                    # the budget. Deleting these was the pipeline throwing away
                    # two thirds of what it had already paid to extract -- 304
                    # frames on a film run that published 152. The decode is
                    # spent either way; keeping them costs disk and nothing
                    # else, and it is what lets a later pass or a human curate
                    # from more than the budget happened to allow.
                    if surplus is not None:
                        surplus.mkdir(parents=True, exist_ok=True)
                        staged.rename(surplus / staged.name)
                    else:
                        staged.unlink()
        # A duplicate record names the frame that represented it. If breadth
        # selection then dropped that frame, the record would point at an ID
        # absent from the bundle -- and a rejected frame could lose the only
        # justification for its rejection. Re-point each orphan at the nearest
        # frame that survived, and record the distance actually measured.
        for omission in redundant_omissions:
            represented_by = omission.get("selected_candidate_id")
            if not represented_by or represented_by in keep_ids:
                continue
            own = signature_of.get(omission["candidate"]["candidate_id"])
            nearest_id, nearest = "", 1.0
            for candidate in kept:
                signature = signature_of.get(candidate.candidate_id)
                if own is None or signature is None:
                    continue
                distance = frame_quality.framing_distance(own, signature)
                if distance < nearest:
                    nearest_id, nearest = candidate.candidate_id, distance
            omission["selected_candidate_id"] = nearest_id
            omission["framing_distance"] = round(nearest, 6)
            omission["relinked_after_breadth_selection"] = True
        for shot in shot_audit:
            shot["published"] = sum(
                1 for candidate in kept if candidate.shot_id == shot["shot_id"])
            if not shot["published"] and shot["outcome"] == "published":
                shot["outcome"] = "measured_but_less_distinct"
    published = sorted(kept, key=lambda c: c.timestamp_seconds)
    return {
        "published": published,
        "extracted_candidates": extracted_candidates,
        "published_frame_quality": measured,
        "breadth_omitted": breadth_omitted,
        "breadth_measured": breadth_measured,
        "quality_omissions": quality_omissions,
        "redundant_omissions": redundant_omissions,
        "shot_audit": shot_audit,
        "unprocessed": unprocessed,
        "capacity_omitted": capacity_omitted,
        "extraction_attempts": extraction_attempts,
        "stop_reason": stop_reason,
        "plan_stopped_by": plan_stopped_by,
    }


#: What the pipeline itself needs from the identity it is given: enough to
#: name the bundle's folder and say what the work is. It is copied into the
#: bundle whole, so anything else in it travels along untouched; a field
#: nobody here reads is not an error. The id fields are the catalogue's ids
#: (Shoko's, or the gallery's own for a file added without Shoko).
IDENTITY_REQUIRED = frozenset({
    "series_title", "entry_type", "source_filename", "series_id",
    "episode_ids", "file_id", "work_id",
})
IDENTITY_ENTRY_TYPES = ("movie", "episode")


def _validate_source_identity(value: dict) -> None:
    """Fail in the first second rather than after the extraction, on the
    few things that would make the result unusable: a missing name or id,
    or an entry type the folder naming does not know."""
    missing = IDENTITY_REQUIRED - value.keys()
    if missing:
        raise ValueError(f"source identity missing: {', '.join(sorted(missing))}")
    if value["entry_type"] not in IDENTITY_ENTRY_TYPES:
        raise ValueError(
            f"source identity entry_type must be one of "
            f"{', '.join(IDENTITY_ENTRY_TYPES)}, not {value['entry_type']!r}"
        )
    if not isinstance(value["episode_ids"], list):
        raise ValueError("source identity episode_ids must be a list")
    if value["entry_type"] == "episode" and not value["episode_ids"]:
        raise ValueError("an episode identity needs at least one episode id")


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="frame-sample-pipeline",
        description="Create an exact-PTS prepared-candidate bundle and palette result",
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--source-identity", required=True, help="source identity JSON object")
    parser.add_argument("--bundle-id", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--library-id", default="hikari-library")
    parser.add_argument("--source-fingerprint-ref", required=True)
    parser.add_argument("--exclusions")
    parser.add_argument("--hold-chapters", action="store_true",
                        help="read the file's chapter markers and hold the "
                             "frames inside chapters that read as an opening "
                             "or ending (named, or 85-95 s near either end): "
                             "extracted into the surplus, kept out of the pick "
                             "(#47). Proposed intervals, never approved ones.")
    parser.add_argument("--density", type=float, default=1.0,
                        help="multiplier on the duration-derived samples per "
                             "shot; 1.0 is one sample per 2 s of shot, capped "
                             "at 3")
    parser.add_argument("--boundary-margin", type=float, default=0.5)
    parser.add_argument("--adaptive-threshold", type=float, default=3.0)
    parser.add_argument("--safety-margin", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-candidates", type=int, default=None,
                        help="published-candidate budget, 1-300. Omitted, it "
                             "is derived from the detected shot count, which "
                             "reproduces research/00's 35-70 for an episode "
                             "and 100-220 for a film from one constant")
    parser.add_argument("--max-extractions", type=int, default=4000,
                        help="hard cap on frames extracted. Sized above the "
                             "largest pool measured. The old default of 64 was "
                             "left from the 8-candidate bundle era and "
                             "silently truncated any real run that omitted "
                             "the flag")
    parser.add_argument("--select-spacing-alpha", type=float,
                        default=SELECT_SPACING_ALPHA,
                        help="temporal spacing applied when choosing the "
                             "published set from measured frames; 0 selects "
                             "on visual distinctness alone")
    parser.add_argument("--spread-alpha", type=float, default=SPREAD_ALPHA,
                        help="how hard to prefer shots far in time from "
                             "one already chosen; 0 ranks on scene score "
                             "alone, which leaves long works with unlit "
                             "stretches")
    parser.add_argument("--min-detail", type=float,
                        default=frame_quality.DEFAULT_MIN_DETAIL,
                        help="reject frames below this edge-energy floor "
                             "(fades and black frames measure ~0)")
    parser.add_argument("--min-entropy", type=float,
                        default=frame_quality.DEFAULT_MIN_ENTROPY,
                        help="reject frames below this luma-entropy floor "
                             "(near-blank cards)")
    parser.add_argument("--intra-shot-distinct", type=float,
                        default=frame_quality.DEFAULT_INTRA_SHOT_DISTINCT,
                        help="framing distance a second frame from the same "
                             "shot must clear to be published")
    parser.add_argument("--cross-shot-duplicate", type=float,
                        default=frame_quality.DEFAULT_CROSS_SHOT_DUPLICATE,
                        help="framing distance below which a frame repeats an "
                             "already-published shot")
    parser.add_argument("--max-per-shot", type=int, default=2,
                        help="hard ceiling on published frames from one shot")
    parser.add_argument("--verify-source", action="store_true",
                        help="sha256 the source and record it as "
                             "source_fingerprint. Off by default: it is a full "
                             "extra read of the source on top of the decode, "
                             "and --source-fingerprint-ref already carries the "
                             "caller's identity assertion")
    parser.add_argument("--dark-luma", type=float,
                        default=frame_quality.DEFAULT_DARK_LUMA,
                        help="mean luma at or below which a frame counts as "
                             "near-black; the quality floor only fires on "
                             "luma-extreme frames")
    parser.add_argument("--bright-luma", type=float,
                        default=frame_quality.DEFAULT_BRIGHT_LUMA,
                        help="mean luma at or above which a frame counts as "
                             "near-white")
    parser.add_argument("--blank-luma-stddev", type=float,
                        default=frame_quality.DEFAULT_BLANK_LUMA_STDDEV,
                        help="luma standard deviation at or below which a "
                             "questioned frame is a blank and is deleted. "
                             "Above it the frame is published with a review "
                             "disposition instead, because no measure at this "
                             "layer separates a title card from a composition")
    parser.add_argument("--passage-start", type=float, default=None,
                        help="optional passage window start in seconds; runs the "
                             "pipeline on a stream-copied slice of the input")
    parser.add_argument("--passage-end", type=float, default=None,
                        help="passage window end in seconds; required with "
                             "--passage-start")
    parser.add_argument("--created-at")
    parser.add_argument("--ffmpeg-path", default="ffmpeg")
    parser.add_argument("--ffprobe-path", default="ffprobe")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    if args.max_extractions < 1:
        print("frame-sample-pipeline: --max-extractions must be a positive integer", file=sys.stderr)
        return 2
    if args.max_candidates is not None and not (
            1 <= args.max_candidates <= MAX_PUBLISHED_CEILING):
        print(f"frame-sample-pipeline: --max-candidates must be between 1 and "
              f"{MAX_PUBLISHED_CEILING}", file=sys.stderr)
        return 2
    # The one flag on this CLI whose value decides whether pixels are destroyed.
    if not 0.0 <= args.blank_luma_stddev <= frame_quality.MAX_BLANK_LUMA_STDDEV:
        print(f"frame-sample-pipeline: --blank-luma-stddev must be between 0 "
              f"and {frame_quality.MAX_BLANK_LUMA_STDDEV}; above that the blank "
              f"reject starts deleting frames a reader has labelled as worth "
              f"keeping", file=sys.stderr)
        return 2
    if (args.passage_start is None) != (args.passage_end is None):
        print("frame-sample-pipeline: --passage-start and --passage-end must be given together", file=sys.stderr)
        return 2
    if args.passage_start is not None and not 0 <= args.passage_start < args.passage_end:
        print("frame-sample-pipeline: passage window must satisfy 0 <= start < end", file=sys.stderr)
        return 2
    source_identity = _load_object(args.source_identity, "source identity")
    _validate_source_identity(source_identity)
    exclusions: List[ExclusionInterval] = []
    if args.exclusions:
        exclusions = intervals_from_dicts(
            _load_object(args.exclusions, "exclusions").get("exclusion_intervals", [])
        )
    chapter_audit: Optional[dict] = None
    if args.hold_chapters:
        if args.passage_start is not None:
            print("frame-sample-pipeline: --hold-chapters reads the source's chapter "
                  "times, which a passage slice does not keep", file=sys.stderr)
            return 2
        chapters, chapter_duration = probe_chapters(args.input, args.ffprobe_path)
        proposed, rows = chapter_intervals(chapters, chapter_duration, args.job_id)
        exclusions = list(exclusions) + proposed
        chapter_audit = {
            "chapters": rows,
            "duration_seconds": round(chapter_duration, 3),
            "held_intervals": [iv.to_dict() for iv in proposed],
            "note": "chapters that read as an opening or ending became proposed "
                    "exclusion intervals (frame_sample.chapters); the frames inside "
                    "them are extracted into the surplus and listed in "
                    "held-candidates.json, and the picker leaves them unless pinned",
        }
        print(f"[chapters] {len(chapters)} chapters, {len(proposed)} held as openings "
              f"or endings", flush=True)

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise RuntimeError("output root must be empty; attempts never overwrite")
    audit_root = output_root / "audit"
    work_root = output_root / "work"
    audit_root.mkdir()
    work_root.mkdir()
    if chapter_audit is not None:
        (audit_root / "chapters.json").write_bytes(_json_bytes(chapter_audit))
    try:
        result = _run_pipeline(args, output_root, audit_root, work_root,
                               source_identity, exclusions)
    except BaseException:
        # Keep the staged bundle. Extraction is by far the most expensive part
        # of a run, and a failure at validation or publish should leave it
        # recoverable rather than force a full re-extract -- which is exactly
        # what a long run failing at the last step would otherwise cost.
        # The passage slice is large and regenerable, so it still goes.
        slice_path = work_root / "passage-slice.mkv"
        if slice_path.exists():
            slice_path.unlink()
        raise
    # A clean run has nothing left worth keeping; the published bundle and the
    # audit trail live outside the work root.
    shutil.rmtree(work_root, ignore_errors=True)
    return result


def _held_shots(held: list[CandidateRecord]) -> dict[str, list[CandidateRecord]]:
    """Held samples by shot, each shot's samples in time order."""
    by_shot: dict[str, list[CandidateRecord]] = {}
    for candidate in held:
        by_shot.setdefault(candidate.shot_id, []).append(candidate)
    for members in by_shot.values():
        members.sort(key=lambda c: c.timestamp_seconds)
    return by_shot


def _held_picks(by_shot: dict[str, list[CandidateRecord]],
                resolved: dict[str, Optional[int]]) -> list[tuple[str, int]]:
    """One (candidate_id, pts) per held shot: the sample nearest the shot's
    middle that resolves to a frame, in pts order with no pts twice, as the
    batch extractor requires. A shot none of whose samples resolve is left
    out (its records stay in held-candidates.json without pixels)."""
    picks: list[tuple[str, int]] = []
    seen: set[int] = set()
    for members in by_shot.values():
        middle = (len(members) - 1) / 2
        for candidate in sorted(members, key=lambda c: abs(members.index(c) - middle)):
            pts = resolved.get(candidate.candidate_id)
            if pts is None or pts in seen:
                continue
            seen.add(pts)
            picks.append((candidate.candidate_id, pts))
            break
    picks.sort(key=lambda item: item[1])
    return picks


def _extract_held(
    held: list[CandidateRecord],
    exclusions: List[ExclusionInterval],
    input_path: str,
    time_base: str,
    work_root: Path,
    surplus_root: Path,
    audit_root: Path,
    bundle_id: str,
    ffmpeg_path: str,
    ffprobe_path: str,
    color_filter: str,
) -> int:
    """Pull one frame per held shot into the surplus and record them all.

    A held candidate (inside a proposed exclusion interval, today a chapter
    that reads as an opening or ending, #47) was sampled and never
    extracted: `held-candidates.json` was timestamps only. Holding is meant
    to keep frames out of the pick, not out of reach, so the middle sample
    of each held shot is extracted exactly like any other frame and placed
    in the surplus beside the breadth-omitted ones. There the pool page
    lists it, a lock brings it into the next pick, and the analyze stage
    embeds it with the rest of the surplus. One per shot keeps the cost to
    a few dozen frames an episode: an opening is a few dozen shots, and a
    person choosing from it wants one frame of each, not every sample.

    Rewrites `held-candidates.json` with the interval each frame sat in and
    whether its pixels were pulled. Returns how many were.
    """
    rows: list[dict] = []
    proposed = [iv for iv in exclusions if iv.is_proposed]

    def interval_of(timestamp: float) -> Optional[ExclusionInterval]:
        for iv in proposed:
            if iv.start_seconds <= timestamp <= iv.end_seconds:
                return iv
        return None

    extracted: set[str] = set()
    by_shot = _held_shots(held)
    if held:
        # Every held sample's PTS in one metadata pass, so a shot whose middle
        # sample has no frame (the picture ending before the container does)
        # can fall back to its next-nearest sample instead of losing the shot.
        ordered = sorted(held, key=lambda c: c.timestamp_seconds)
        resolved = dict(zip(
            (c.candidate_id for c in ordered),
            _resolve_pts_bulk(input_path, [c.timestamp_seconds for c in ordered], time_base, ffprobe_path),
        ))
        batch = _held_picks(by_shot, resolved)
        if batch:
            held_stage = Path(tempfile.mkdtemp(prefix=f".{bundle_id}.held.", dir=work_root))
            try:
                _extract_batch(input_path, batch, held_stage, ffmpeg_path, color_filter, time_base)
                surplus_root.mkdir(parents=True, exist_ok=True)
                for candidate_id, _pts in batch:
                    produced = held_stage / f"{candidate_id}.png"
                    if produced.is_file() and produced.stat().st_size > 0:
                        produced.rename(surplus_root / produced.name)
                        extracted.add(candidate_id)
            finally:
                shutil.rmtree(held_stage, ignore_errors=True)
            print(f"[extract] held {len(held)} samples in {len(by_shot)} shots; "
                  f"{len(extracted)} frames pulled into the surplus", flush=True)
    for candidate in sorted(held, key=lambda c: c.timestamp_seconds):
        iv = interval_of(candidate.timestamp_seconds)
        row = candidate.to_dict()
        row.update({
            "interval_id": iv.interval_id if iv else None,
            "reason": iv.reason if iv else None,
            "pixel": candidate.candidate_id in extracted,
        })
        rows.append(row)
    (audit_root / "held-candidates.json").write_bytes(_json_bytes({
        "pixel_artifacts_published": bool(extracted),
        "pixel_location": f"surplus/{bundle_id}",
        "note": "frames inside a proposed exclusion interval: sampled, kept out "
                "of the pick, and (one per shot, pixel: true) extracted into the "
                "surplus so the pool shows them and a lock brings one in",
        "candidates": rows,
    }))
    return len(extracted)


def _run_pipeline(args, output_root: Path, audit_root: Path,
                  work_root: Path, source_identity: dict,
                  exclusions: List[ExclusionInterval]) -> int:

    input_path = args.input
    if args.passage_start is not None:
        slice_path = work_root / "passage-slice.mkv"
        _run([
            args.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-ss", str(args.passage_start), "-to", str(args.passage_end),
            "-i", args.input, "-map", "0:v:0", "-c", "copy",
            "-avoid_negative_ts", "make_zero", str(slice_path),
        ])
        (audit_root / "passage.json").write_bytes(_json_bytes({
            "requested_start_seconds": args.passage_start,
            "requested_end_seconds": args.passage_end,
            "note": "pipeline ran on a stream-copied slice of the source; "
                    "candidate PTS values are slice-relative, and the copy "
                    "begins at the keyframe at or before the requested start",
        }))
        input_path = str(slice_path)

    created_at = args.created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = SamplerParams(
        density=args.density,
        boundary_margin_seconds=args.boundary_margin,
        adaptive_threshold=args.adaptive_threshold,
        safety_margin_seconds=args.safety_margin,
        seed=args.seed,
    )
    sampler = FrameSampler(args.ffmpeg_path, args.ffprobe_path, args.seed)
    # Stream geometry for the manifest's source_stream. Probed from input_path,
    # so under a passage window this describes the stream-copied slice -- which
    # carries the source's dimensions and aspect ratios unchanged.
    phase_seconds: dict[str, float] = {}
    phase_start = time.perf_counter()
    stream_probe = sampler.probe(input_path)
    source_color = SourceColorInfo.from_probe(stream_probe)
    applied_color_rule = color_rule(source_color)
    color_filter = build_color_filter(source_color)
    # Always the real source, never the passage slice: a slice's digest is not
    # the source's identity, and recording one under that name is worse than
    # recording none.
    selection = sampler.run(
        input_path, exclusions=exclusions, params=params,
        fingerprint_path=args.input if args.verify_source else None,
    )
    (audit_root / "selection.json").write_bytes(selection.to_json(created_at=created_at) + b"\n")

    cleared, held = _partition_candidates(selection.candidates)
    # Derive the budget now that the shot count is known, unless the
    # operator set one. Everything downstream -- the shot plan, the publish
    # cap -- reads this rather than args.
    max_candidates = args.max_candidates
    if max_candidates is None:
        detected_shots = len({c.shot_id for c in selection.candidates})
        max_candidates = max(BUDGET_FLOOR, min(
            MAX_PUBLISHED_CEILING,
            round(detected_shots * PUBLISH_FRAMES_PER_SHOT * REVIEW_OVERHEAD),
        ))
        print(f"[select] {detected_shots} shots detected; publish budget "
              f"{max_candidates} (derived)", flush=True)
    (audit_root / "held-candidates.json").write_bytes(_json_bytes({
        "pixel_artifacts_published": False,
        "candidates": [candidate.to_dict() for candidate in held],
    }))
    if not cleared:
        if held:
            raise RuntimeError(
                "every sampled frame sits inside a held interval (the file's "
                "chapters, or the exclusions given), so there is nothing to "
                "pick from; onboard it again without the hold")
        raise RuntimeError("selection produced no cleared candidates")

    stream_index, time_base = _probe_stream(input_path, args.ffprobe_path)
    stage = Path(tempfile.mkdtemp(prefix=f".{args.bundle_id}.staging.", dir=work_root))
    final_bundle = work_root / args.bundle_id
    # Beside the bundle, never inside it. The bundle is checksum-bound and is
    # the reduced set the models run on; this is the remainder for whoever
    # curates later.
    surplus_root = output_root / "surplus" / args.bundle_id

    # Scene detection decodes the whole episode; so does extraction.
    phase_seconds["selection"] = round(time.perf_counter() - phase_start, 3)
    phase_start = time.perf_counter()
    result = _publish_best_in_shot(
        cleared=cleared,
        input_path=input_path,
        time_base=time_base,
        stream_index=stream_index,
        stage=stage,
        surplus=surplus_root,
        ffmpeg_path=args.ffmpeg_path,
        ffprobe_path=args.ffprobe_path,
        color_filter=color_filter,
        max_candidates=max_candidates,
        max_extractions=args.max_extractions,
        spread_alpha=args.spread_alpha,
        select_spacing_alpha=args.select_spacing_alpha,
        min_detail=args.min_detail,
        min_entropy=args.min_entropy,
        intra_shot_distinct=args.intra_shot_distinct,
        cross_shot_duplicate=args.cross_shot_duplicate,
        max_per_shot=args.max_per_shot,
        dark_luma=args.dark_luma,
        bright_luma=args.bright_luma,
        blank_luma_stddev=args.blank_luma_stddev,
        source_duration=float(stream_probe.get("duration") or 0.0),
    )
    published = result["published"]
    held_extracted = _extract_held(
        held, exclusions, input_path, time_base, work_root, surplus_root,
        audit_root, args.bundle_id, args.ffmpeg_path, args.ffprobe_path,
        color_filter,
    )
    extracted_candidates = result["extracted_candidates"]
    quality_omissions = result["quality_omissions"]
    redundant_omissions = result["redundant_omissions"]
    unprocessed = result["unprocessed"]
    capacity_omitted = result["capacity_omitted"]
    extraction_attempts = result["extraction_attempts"]

    candidates = [extracted_candidates[c.candidate_id] for c in published]
    (audit_root / "shot-coverage.json").write_bytes(_json_bytes({
        "policy": "one_published_frame_per_shot_plus_distinct_second",
        "intra_shot_distinct_threshold": args.intra_shot_distinct,
        "cross_shot_duplicate_threshold": args.cross_shot_duplicate,
        "shots": result["shot_audit"],
        "published_frame_quality": result["published_frame_quality"],
    }))
    (audit_root / "quality-omitted-candidates.json").write_bytes(_json_bytes({
        "reason": "frame_quality_floor",
        "note": "the floor only fires on luma-extreme frames, so a well-exposed "
                "but deliberately soft image is never questioned here. Of the "
                "frames it does question, only those flat enough to be a fade "
                "or a flash are deleted and listed below. The rest carry a "
                "review disposition and appear in shot-coverage.json if they "
                "were published, or in breadth-omitted-candidates.json if "
                "selection then dropped them",
        "min_detail": args.min_detail,
        "min_entropy": args.min_entropy,
        "dark_luma": args.dark_luma,
        "bright_luma": args.bright_luma,
        "blank_luma_stddev": args.blank_luma_stddev,
        "pixel_artifacts_published": False,
        "candidates": quality_omissions,
    }))
    (audit_root / "duplicate-omitted-candidates.json").write_bytes(_json_bytes({
        "reason": "framing_distance",
        "pixel_artifacts_published": False,
        "candidates": redundant_omissions,
    }))
    (audit_root / "breadth-omitted-candidates.json").write_bytes(_json_bytes({
        "reason": "less_distinct_than_the_published_set",
        "note": "measured and quality-checked, then not published because a "
                "frame already kept was visually closer to it than the budget "
                "allowed. These are distinct frames that cleared the floor and "
                "lost only on capacity, so the pixels are KEPT under "
                "surplus/<bundle_id>/ beside the bundle rather than unlinked. "
                "They are deliberately outside the checksum-bound bundle: the "
                "models run on the reduced set only, and this is "
                "the remainder for a later curation pass or a human.",
        "pixel_artifacts_published": True,
        "pixel_location": f"surplus/{args.bundle_id}",
        "candidates": [
            dict(c.to_dict(), **result["breadth_measured"].get(c.candidate_id, {}))
            for c in result["breadth_omitted"]
        ],
    }))
    (audit_root / "capacity-omitted-candidates.json").write_bytes(_json_bytes({
        "reason": "prepared_bundle_candidate_limit",
        "pixel_artifacts_published": False,
        "candidates": [candidate.to_dict() for candidate in capacity_omitted],
    }))
    (audit_root / "unprocessed-candidates.json").write_bytes(_json_bytes({
        # Which bound actually stopped the plan. `shot_plan_budget` is the run
        # working as designed; `extraction_attempt_limit` means it was cut short
        # of its own measurement target and the results are a truncation.
        "reason": result["plan_stopped_by"] or "shot_plan_budget",
        "truncated": result["plan_stopped_by"] == "extraction_attempt_limit",
        "extraction_attempts": extraction_attempts,
        "max_extractions": args.max_extractions,
        "pixel_artifacts_published": False,
        "candidates": [candidate.to_dict() for candidate in unprocessed],
    }))

    manifest = {
        "schema_version": "2.4",
        "bundle_id": args.bundle_id,
        "job_id": args.job_id,
        "library_id": args.library_id,
        "source_fingerprint_ref": args.source_fingerprint_ref,
        "created_at": created_at,
        "pipeline": {"version": PIPELINE_VERSION, "digest": _pipeline_digest()},
        "extraction": {
            "provider": "ffmpeg-cli",
            "policy_version": EXTRACTION_POLICY,
            "ffmpeg_build": _tool_build(args.ffmpeg_path),
            "ffprobe_build": _tool_build(args.ffprobe_path),
        },
        "preprocessing_policy_version": PREPROCESSING_POLICY,
        "source_identity": source_identity,
        "source_stream": {
            "width": stream_probe["width"],
            "height": stream_probe["height"],
            "sample_aspect_ratio": stream_probe["sample_aspect_ratio"],
            "display_aspect_ratio": stream_probe["display_aspect_ratio"],
            "declared_color": {
                **stream_probe["declared_color"],
                "rule": applied_color_rule,
            },
        },
        "color_descriptor": {
            "provider": "palette-descriptor",
            "provider_version": "0.2.0",
        },
        "scene_score_provenance": selection.scene_score_provenance,
        "exclusion_intervals": selection.exclusion_intervals,
        "candidates": candidates,
    }
    (stage / "manifest.json").write_bytes(_json_bytes(manifest))
    manifest_sha = _sha256(stage / "manifest.json")
    (stage / "READY.json").write_bytes(_json_bytes({
        "schema_version": "2.4",
        "state": "ready",
        "bundle_id": args.bundle_id,
        "manifest_sha256": manifest_sha,
        "bundle_digest": _bundle_digest(manifest_sha),
        "completion_id": f"ready-{manifest_sha}",
    }))
    stage.rename(final_bundle)

    phase_seconds["extraction"] = round(time.perf_counter() - phase_start, 3)
    phase_start = time.perf_counter()
    # Check the bundle is whole, copy it under prepared/ with readable
    # names, and describe each frame's colours (bundle.py).
    bundle_steps.check(final_bundle)
    prepared_dir = bundle_steps.publish(final_bundle, output_root, _bundle_digest)
    bundle_steps.describe_colors(
        prepared_dir, output_root / "results",
        tool={"version": PIPELINE_VERSION, "digest": _pipeline_digest()},
        preprocessing=PREPROCESSING_POLICY, log=print)

    phase_seconds["validate_publish_color"] = round(
        time.perf_counter() - phase_start, 3)
    summary = {
        "schema_version": "1.0",
        "state": "complete",
        "prepared_bundle": str(prepared_dir.relative_to(output_root)),
        "palette_result": f"results/completed/{args.bundle_id}",
        "selected_candidates": len(selection.candidates),
        "published_candidates": len(published),
        "published_shots": sum(1 for shot in result["shot_audit"] if shot["published"]),
        "held_candidates": len(held),
        # Held frames pulled into the surplus (one per held shot) so the pool
        # shows them and a lock can bring one in; the rest are timestamps only.
        "held_extracted": held_extracted,
        "quality_omitted_candidates": len(quality_omissions),
        # Published, but luma-extreme and featureless enough that the floor
        # questioned them. Nothing at this layer tells a title card from a
        # composition, so the human review queue is this number.
        "review_disposition_candidates": sum(
            1 for entry in result["published_frame_quality"].values()
            if entry.get("floor_disposition") == frame_quality.REVIEW),
        # Every frame the floor questioned and did not delete, whether or not
        # it survived breadth selection. Counting only the published ones made
        # the split look cheaper than it is.
        "review_disposition_measured": sum(
            1 for entry in list(result["published_frame_quality"].values())
            + [m["frame_quality"] or {} for m in result["breadth_measured"].values()]
            if entry.get("floor_disposition") == frame_quality.REVIEW),
        "duplicate_omitted_candidates": len(redundant_omissions),
        "capacity_omitted_candidates": len(capacity_omitted),
        # Measured, then not published because something already kept was
        # visually nearer. Without this the selection partition silently
        # stopped balancing the moment widening discarded anything.
        "breadth_omitted_candidates": len(result["breadth_omitted"]),
        "unprocessed_candidates": len(unprocessed),
        "extraction_attempts": extraction_attempts,
        "max_extractions": args.max_extractions,
        # The episode is decoded twice, once for detection and once for
        # extraction. Which dominates decides whether moving decode to a GPU
        # is worth the frame-parity proof it would need.
        "phase_seconds": phase_seconds,
    }
    (output_root / "PIPELINE_READY.json").write_bytes(_json_bytes(summary))
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"frame-sample-pipeline: {error}", file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            print(error.stderr, file=sys.stderr)
        sys.exit(1)
