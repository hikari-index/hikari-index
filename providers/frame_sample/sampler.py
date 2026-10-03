"""Deterministic frame-sampler: selects candidate timestamps from a source timeline."""

from __future__ import annotations

import hashlib
import json
import random
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .exclusion import ExclusionInterval, apply_exclusions, merge_safety_margins
from .provenance import SceneScoreProvenance
from .scene_score import SceneBoundary, SceneScoreProvenanceData


@dataclass(frozen=True)
class SamplerParams:
    # density multiplies the duration-derived sample count; 1.0 is that count.
    density: float = 1.0
    boundary_margin_seconds: float = 0.5
    adaptive_threshold: float = 3.0
    # Off by default: real scene scores run 70-124 against an adaptive_threshold
    # of 3.0, so this fired on every shot and added a third near-identical
    # sample. Best-in-shot publishing supersedes it.
    extra_per_boundary: int = 0
    safety_margin_seconds: float = 2.0
    seed: int = 42
    dedup_phash_threshold: int = 5
    min_sample_spacing_seconds: float = 2.0
    max_samples_per_shot: int = 3


@dataclass
class CandidateRecord:
    candidate_id: str
    shot_id: str
    timestamp_seconds: float
    scene_score: float
    exclusion_status: str

    def to_dict(self) -> dict:
        d: dict = {
            "candidate_id": self.candidate_id,
            "shot_id": self.shot_id,
            "timestamp_seconds": self.timestamp_seconds,
            "scene_score": self.scene_score,
            "exclusion_status": self.exclusion_status,
        }
        return d


@dataclass
class SamplerResult:
    candidates: List[CandidateRecord]
    dedup_families: List[dict]
    scene_score_provenance: Optional[dict]
    exclusion_intervals: List[dict]
    parameters: dict
    source_fingerprint: str = ""

    def to_dict(self, created_at: Optional[str] = None) -> dict:
        d: dict = {
            "schema_version": "1.2",
            "provider": "frame-sample",
            "provider_version": "0.1.0",
            "parameters": self.parameters,
            "candidates": [c.to_dict() for c in self.candidates],
            "dedup_families": self.dedup_families,
        }
        if self.scene_score_provenance is not None:
            d["scene_score_provenance"] = self.scene_score_provenance
        if self.exclusion_intervals:
            d["exclusion_intervals"] = self.exclusion_intervals
        if self.source_fingerprint:
            d["source_fingerprint"] = self.source_fingerprint
        if created_at is not None:
            d["created_at"] = created_at
        return d

    def to_json(self, created_at: Optional[str] = None) -> bytes:
        return json.dumps(
            self.to_dict(created_at=created_at),
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
        ).encode("utf-8")


def _fingerprint_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def samples_for_shot(sample_range: float, params: SamplerParams) -> int:
    """How many samples a shot earns, scaled to how long it runs.

    A flat per-shot count is what manufactured most near-twins: a 1.5 s cut has
    only a 0.5 s sample range once boundary margins are taken, so three samples
    landed 80-200 ms apart and were the same picture by construction. Spacing
    the samples instead means a short cut yields one frame and a long take
    yields several genuinely different ones.
    """
    if params.min_sample_spacing_seconds <= 0:
        natural = params.max_samples_per_shot
    else:
        natural = 1 + int(sample_range // params.min_sample_spacing_seconds)
    scaled = int(round(natural * params.density))
    return max(1, min(params.max_samples_per_shot, scaled))


def select_candidates(
    duration_seconds: float,
    boundaries: List[SceneBoundary],
    exclusions: List[ExclusionInterval],
    params: SamplerParams,
) -> Tuple[List[CandidateRecord], List[Tuple[float, str]]]:
    rng = random.Random(params.seed)
    merged = merge_safety_margins(exclusions, params.safety_margin_seconds)

    free_regions: List[Tuple[float, float]] = []
    cursor = 0.0
    for s, e, _ids in merged:
        if s > cursor:
            free_regions.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration_seconds:
        free_regions.append((cursor, duration_seconds))

    shots = _build_shots(duration_seconds, boundaries)
    all_timestamps: List[Tuple[float, str, float]] = []

    for region_start, region_end in free_regions:
        region_dur = region_end - region_start
        if region_dur <= 0:
            continue

        for shot_idx, (shot_start, shot_end, shot_score) in enumerate(shots):
            if shot_start >= region_end or shot_end <= region_start:
                continue

            effective_start = max(shot_start, region_start)
            effective_end = min(shot_end, region_end)
            shot_dur = effective_end - effective_start
            if shot_dur <= 0:
                continue

            shot_id = f"shot-{shot_idx:03d}"
            margin = params.boundary_margin_seconds
            sample_start = effective_start + margin
            sample_end = effective_end - margin
            if sample_end <= sample_start:
                continue

            sample_range = sample_end - sample_start
            n_base = samples_for_shot(sample_range, params)
            if n_base == 1:
                jitter_range = sample_range * 0.1
                ts = sample_start + sample_range * 0.5 + rng.uniform(-jitter_range, jitter_range)
                ts = max(sample_start, min(sample_end, ts))
                all_timestamps.append((ts, shot_id, shot_score))
            else:
                step = sample_range / n_base
                for i in range(n_base):
                    jitter = rng.uniform(-step * 0.1, step * 0.1)
                    ts = sample_start + step * (i + 0.5) + jitter
                    ts = max(sample_start, min(sample_end, ts))
                    all_timestamps.append((ts, shot_id, shot_score))

            if shot_score > params.adaptive_threshold:
                for _ in range(params.extra_per_boundary):
                    offset = rng.uniform(-0.2, 0.2)
                    extra_ts = max(sample_start, min(sample_end, effective_start + shot_dur * 0.5 + offset))
                    all_timestamps.append((extra_ts, shot_id, shot_score))

    all_timestamps.sort(key=lambda x: x[0])

    raw_timestamps = [t[0] for t in all_timestamps]
    kept_ts, held_ts = apply_exclusions(raw_timestamps, exclusions, params.safety_margin_seconds)

    kept_set = set()
    for ts in kept_ts:
        kept_set.add(ts)

    held_map: Dict[float, str] = {}
    for ts, interval_id in held_ts:
        held_map[ts] = interval_id

    candidates: List[CandidateRecord] = []
    cand_idx = 0
    for ts, shot_id, score in all_timestamps:
        if ts in held_map:
            status = "held_unresolved"
        elif ts in kept_set:
            status = "cleared"
        else:
            continue
        cand_idx += 1
        candidates.append(CandidateRecord(
            candidate_id=f"cand-{cand_idx:04d}",
            shot_id=shot_id,
            timestamp_seconds=round(ts, 6),
            scene_score=round(score, 6),
            exclusion_status=status,
        ))

    return candidates, held_ts


def _build_shots(
    duration_seconds: float,
    boundaries: List[SceneBoundary],
) -> List[Tuple[float, float, float]]:
    if not boundaries:
        return [(0.0, duration_seconds, 0.0)]

    shots: List[Tuple[float, float, float]] = []
    prev_ts = 0.0
    prev_score = 0.0

    for boundary in sorted(boundaries, key=lambda b: b.timestamp):
        if boundary.timestamp > prev_ts:
            shots.append((prev_ts, boundary.timestamp, prev_score))
        prev_ts = boundary.timestamp
        prev_score = boundary.score

    if prev_ts < duration_seconds:
        shots.append((prev_ts, duration_seconds, prev_score))

    return shots


#: Sources this short may legitimately be a single continuous take, so the
#: plausibility check does not apply to them.
MIN_DURATION_FOR_DETECTION_CHECK = 120.0
#: Measured shot density across five real episodes ran 12.2 to 25.8 shots per
#: minute. One per minute is far below anything observed and is not a threshold
#: any real episode approaches.
MIN_SHOTS_PER_MINUTE = 1.0


def _assert_detection_plausible(video_path, duration, boundaries) -> None:
    """Refuse to call a run complete when nothing was actually detected.

    A silent decode failure is worse than a loud one. OpenCV read zero frames
    from an AV1 source without raising, so a 28-minute episode produced one shot
    and three candidates and the run still reported `state: complete` -- a
    result that looks like data and is not. Extraction still worked, because it
    goes through a different decoder, which is exactly what made the failure
    quiet.
    """
    if duration < MIN_DURATION_FOR_DETECTION_CHECK:
        return
    minutes = duration / 60.0
    if len(boundaries) >= MIN_SHOTS_PER_MINUTE * minutes:
        return
    raise RuntimeError(
        f"scene detection found {len(boundaries)} boundaries in "
        f"{minutes:.1f} minutes of {video_path}, far below the "
        f"{MIN_SHOTS_PER_MINUTE:.0f}/minute floor. Treat this as a decode "
        f"failure rather than a quiet episode: check that the video backend "
        f"can read this codec."
    )


class FrameSampler:
    def __init__(
        self,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        seed: int = 42,
    ):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self.seed = seed

    def probe(self, video_path: str) -> dict:
        from .scene_score import probe_video
        return probe_video(video_path, self.ffprobe_path)

    def compute_scene_scores(
        self,
        video_path: str,
        adaptive_threshold: float = 3.0,
        min_scene_len: int = 15,
    ) -> Tuple[List[SceneBoundary], SceneScoreProvenanceData]:
        from .scene_score import compute_scene_scores
        return compute_scene_scores(
            video_path, self.ffmpeg_path, adaptive_threshold, min_scene_len
        )

    def select(
        self,
        duration_seconds: float,
        boundaries: List[SceneBoundary],
        exclusions: List[ExclusionInterval],
        params: Optional[SamplerParams] = None,
    ) -> Tuple[List[CandidateRecord], List[Tuple[float, str]]]:
        if params is None:
            params = SamplerParams(seed=self.seed)
        return select_candidates(duration_seconds, boundaries, exclusions, params)

    def extract_frame(self, video_path: str, timestamp_seconds: float) -> bytes:
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-ss", f"{timestamp_seconds:.6f}",
            "-i", video_path,
            "-frames:v", "1",
            "-f", "image2pipe",
            "-vcodec", "png",
            "-pix_fmt", "rgb24",
            "-",
        ]
        result = subprocess.run(cmd, capture_output=True, check=True)
        return result.stdout

    def run(
        self,
        video_path: str,
        exclusions: Optional[List[ExclusionInterval]] = None,
        params: Optional[SamplerParams] = None,
        extract_frames: bool = False,
        fingerprint_path: Optional[str] = None,
    ) -> SamplerResult:
        """Sample candidates from `video_path`.

        `fingerprint_path` names what to hash for `source_fingerprint`, and is
        deliberately separate from `video_path`. Under a passage window the
        pipeline hands this method a stream-copied slice, so hashing its input
        recorded the digest of a temporary file under a key that reads as the
        source's identity. Callers that want a fingerprint pass the real source;
        callers that do not pay nothing.
        """
        if exclusions is None:
            exclusions = []
        if params is None:
            params = SamplerParams(seed=self.seed)

        info = self.probe(video_path)
        duration = info["duration"]
        boundaries, prov_data = self.compute_scene_scores(
            video_path, adaptive_threshold=params.adaptive_threshold
        )
        _assert_detection_plausible(video_path, duration, boundaries)
        provenance = SceneScoreProvenance.from_scene_score_data(prov_data)

        candidates, held = self.select(duration, boundaries, exclusions, params)

        dedup_families: List[dict] = []
        if extract_frames:
            hashes: List[Tuple[str, str]] = []
            for c in candidates:
                png = self.extract_frame(video_path, c.timestamp_seconds)
                from .dedup import compute_phash_bytes
                h = compute_phash_bytes(png)
                hashes.append((c.candidate_id, h))
            from .dedup import group_dedup_families
            families = group_dedup_families(hashes, params.dedup_phash_threshold)
            dedup_families = [
                {"family_id": f.family_id, "candidate_ids": f.candidate_ids, "phash": f.phash}
                for f in families
            ]

        # Off unless a path is named. Hashing the input on every run meant a
        # full read of the source in addition to the decode -- double the I/O,
        # and on a NAS a second reason to wake a spun-down disk. The caller already asserts
        # identity through --source-fingerprint-ref.
        fingerprint = _fingerprint_file(fingerprint_path) if fingerprint_path else ""

        return SamplerResult(
            candidates=candidates,
            dedup_families=dedup_families,
            scene_score_provenance=provenance.to_dict(),
            exclusion_intervals=[iv.to_dict() for iv in exclusions],
            parameters={
                "density": params.density,
                "boundary_margin_seconds": params.boundary_margin_seconds,
                "adaptive_threshold": params.adaptive_threshold,
                "extra_per_boundary": params.extra_per_boundary,
                "safety_margin_seconds": params.safety_margin_seconds,
                "seed": params.seed,
                "min_sample_spacing_seconds": params.min_sample_spacing_seconds,
                "max_samples_per_shot": params.max_samples_per_shot,
            },
            source_fingerprint=fingerprint,
        )
