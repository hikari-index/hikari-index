"""CLI entry point for the deterministic frame sampler."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import List, Optional

from .exclusion import ExclusionInterval, intervals_from_dicts
from .sampler import FrameSampler, SamplerParams


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="frame-sample",
        description="Deterministic frame sampler for Hikari Index",
    )
    parser.add_argument("--input", required=True, help="path to source video")
    parser.add_argument("--output", required=True, help="path for output JSON manifest")
    parser.add_argument("--exclusions", default=None, help="path to exclusion intervals JSON")
    parser.add_argument("--density", type=float, default=0.5)
    parser.add_argument("--boundary-margin", type=float, default=0.5)
    parser.add_argument("--adaptive-threshold", type=float, default=3.0,
                        help="PySceneDetect adaptive-detector threshold; also gates per-shot extras")
    parser.add_argument("--safety-margin", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ffmpeg-path", default="ffmpeg")
    parser.add_argument("--ffprobe-path", default="ffprobe")
    parser.add_argument("--created-at", default=None, help="fixed ISO-8601 timestamp for determinism")
    parser.add_argument("--extract-frames", action="store_true", help="extract actual frames (requires FFmpeg decode)")

    args = parser.parse_args(argv)

    exclusions: List[ExclusionInterval] = []
    if args.exclusions:
        with open(args.exclusions) as f:
            data = json.load(f)
        exclusions = intervals_from_dicts(data.get("exclusion_intervals", []))

    params = SamplerParams(
        density=args.density,
        boundary_margin_seconds=args.boundary_margin,
        adaptive_threshold=args.adaptive_threshold,
        safety_margin_seconds=args.safety_margin,
        seed=args.seed,
    )

    sampler = FrameSampler(
        ffmpeg_path=args.ffmpeg_path,
        ffprobe_path=args.ffprobe_path,
        seed=args.seed,
    )

    result = sampler.run(
        video_path=args.input,
        exclusions=exclusions,
        params=params,
        extract_frames=args.extract_frames,
    )

    created_at = args.created_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    output_bytes = result.to_json(created_at=created_at)

    with open(args.output, "wb") as f:
        f.write(output_bytes)

    print(f"wrote {len(result.candidates)} candidates to {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
