"""Scene-score computation via PySceneDetect AdaptiveDetector."""

from __future__ import annotations

import subprocess
import json
import time
from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class SceneBoundary:
    timestamp: float
    score: float


@dataclass(frozen=True)
class SceneScoreProvenanceData:
    detector: str
    detector_version: str
    parameters: dict
    content_metric: str
    #: Which video reader decoded the source for scoring. OpenCV is the default
    #: and cannot decode every codec; see `open_scene_video`. This only affects
    #: boundary detection -- published pixels are always extracted through the
    #: ffmpeg CLI, which remains the frame-identity and colour reference.
    decode_backend: str = "opencv"


def probe_video(video_path: str, ffprobe_path: str = "ffprobe") -> dict:
    cmd = [
        ffprobe_path,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    info = json.loads(result.stdout)
    duration = float(info["format"]["duration"])
    video_stream = None
    for s in info["streams"]:
        if s["codec_type"] == "video":
            video_stream = s
            break
    if video_stream is None:
        raise ValueError(f"no video stream in {video_path}")
    fps_parts = video_stream.get("r_frame_rate", "24/1").split("/")
    fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])
    return {
        "duration": duration,
        "fps": fps,
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "stream_index": int(video_stream["index"]),
        # Aspect belongs to the source stream, not to a reviewed label. Report
        # only what ffprobe states; a missing ratio stays null rather than being
        # inferred from the pixel dimensions, which would assume square pixels.
        "sample_aspect_ratio": _aspect(video_stream.get("sample_aspect_ratio")),
        "display_aspect_ratio": _aspect(video_stream.get("display_aspect_ratio")),
        # What the file itself claims about colour. Null where it claims
        # nothing, which is the signal that the extraction policy must use its
        # coded-height fallback rather than read an interpretation from tags.
        "declared_color": {
            "range": _tag(video_stream.get("color_range")),
            "space": _tag(video_stream.get("color_space")),
            "transfer": _tag(video_stream.get("color_transfer")),
            "primaries": _tag(video_stream.get("color_primaries")),
        },
    }


def _tag(value) -> str | None:
    """ffprobe writes `unknown`/`unspecified` for an absent colour tag."""
    if not value or value in ("unknown", "unspecified", "N/A"):
        return None
    return str(value)


def _aspect(value) -> str | None:
    if not value or value in ("N/A", "0:1"):
        return None
    return str(value)


#: How many frames must decode before a reader is believed. OpenCV reported
#: success and then read zero frames on an AV1 source, which produced a run
#: with one shot that still called itself complete.
BACKEND_PROBE_FRAMES = 2


def open_scene_video(video_path: str):
    """Open the source for scoring, falling back when a reader cannot decode it.

    OpenCV is the default backend and does not fail loudly. On an AV1 source its
    bundled FFmpeg selects the native `av1` decoder, which is hardware-only, and
    every frame read returns nothing -- no exception, just an empty video. A
    28-minute episode then yields one shot and the run reports success.

    So the reader is probed rather than trusted: if it cannot produce a couple
    of frames, PyAV is tried, which carries a software AV1 decoder. Returns the
    opened video positioned back at the start, and the backend name for the run
    record.
    """
    from scenedetect import open_video

    for backend in ("opencv", "pyav"):
        try:
            video = open_video(video_path, backend=backend)
        except Exception:
            continue
        decoded = 0
        while decoded < BACKEND_PROBE_FRAMES:
            if video.read() is False:
                break
            decoded += 1
        if decoded >= BACKEND_PROBE_FRAMES:
            video.reset()
            return video, backend
    raise RuntimeError(
        f"no video backend could decode {video_path}; tried opencv and pyav"
    )


#: Wall seconds between scene-detection progress lines. Detection decodes the
#: whole source and printed nothing until it finished -- 22 minutes of silence
#: on a 155-minute 4K feature, which reads as a hang and has caused runs to be
#: cancelled that were working. Throttled by wall time rather than frame count
#: so the cadence is the same on a 22-minute episode and a feature.
PROGRESS_INTERVAL_SECONDS = 15.0


def compute_scene_scores(
    video_path: str,
    ffmpeg_path: str = "ffmpeg",
    adaptive_threshold: float = 3.0,
    min_scene_len: int = 15,
    progress: bool = True,
) -> Tuple[List[SceneBoundary], SceneScoreProvenanceData]:
    import scenedetect
    from scenedetect import SceneManager, open_video, AdaptiveDetector, StatsManager

    detector_version = getattr(scenedetect, "__version__", "unknown")
    video, backend = open_scene_video(video_path)
    stats_mgr = StatsManager()
    scene_mgr = SceneManager(stats_manager=stats_mgr)
    detector = AdaptiveDetector(
        adaptive_threshold=adaptive_threshold,
        min_scene_len=min_scene_len,
    )
    scene_mgr.add_detector(detector)

    callback = None
    if progress:
        try:
            total = float(video.duration.seconds)
        except Exception:
            total = 0.0
        print(f"[select] scene detection over "
              f"{total / 60:.1f} min via {backend}", flush=True)
        state = {"next": time.monotonic() + PROGRESS_INTERVAL_SECONDS}
        started = time.monotonic()

        def callback(_frame, timecode) -> None:  # noqa: F811
            # Runs per decoded frame: do nothing here but compare two floats
            # until the interval elapses. The frame array is never touched.
            now = time.monotonic()
            if now < state["next"]:
                return
            state["next"] = now + PROGRESS_INTERVAL_SECONDS
            at = timecode.seconds
            elapsed = now - started
            speed = at / elapsed if elapsed > 0 else 0.0
            eta = (total - at) / speed if speed > 0 and total > at else 0.0
            pct = f"{100 * at / total:.0f}%" if total else "?"
            # No cut count here: SceneManager only populates its scene list once
            # detection finishes, so it reads 0 the whole way and looks broken.
            print(f"[select] {at / 60:6.1f} / {total / 60:.1f} min ({pct}), "
                  f"{speed:.1f}x realtime, ~{eta / 60:.0f} min left",
                  flush=True)

    scene_mgr.detect_scenes(video, callback=callback)
    scene_list = scene_mgr.get_scene_list()

    boundaries: List[SceneBoundary] = []
    for start, _end in scene_list:
        ts = start.seconds
        if ts > 0:
            score = _get_content_val_at_timecode(stats_mgr, start)
            boundaries.append(SceneBoundary(timestamp=ts, score=score))

    provenance = SceneScoreProvenanceData(
        detector="pyscenedetect-adaptive",
        detector_version=detector_version,
        parameters={
            "adaptive_threshold": adaptive_threshold,
            "min_scene_len": min_scene_len,
        },
        content_metric="content_val",
        decode_backend=backend,
    )
    return boundaries, provenance


def _get_content_val_at_timecode(stats_mgr, timecode) -> float:
    try:
        metrics = stats_mgr._frame_metrics.get(timecode)
        if metrics and 'content_val' in metrics:
            return float(metrics['content_val'])
    except (AttributeError, KeyError, TypeError):
        pass
    return 0.0
