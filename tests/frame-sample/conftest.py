"""Shared fixtures for frame-sampler tests."""

from __future__ import annotations

import os
import subprocess
import sys
import shutil
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers" / "frame_sample"
sys.path.insert(0, str(PROVIDER_ROOT.parent))

from frame_sample.exclusion import ExclusionInterval
from frame_sample.scene_score import SceneBoundary
from frame_sample.sampler import SamplerParams


@pytest.fixture
def no_exclusions():
    return []


@pytest.fixture
def approved_op():
    return ExclusionInterval(
        interval_id="op-1",
        start_seconds=0.0,
        end_seconds=90.0,
        reason="opening_theme",
        source="chapter_marker",
        confidence="high",
        run="test-run",
        status="exclusion_approved",
    )


@pytest.fixture
def approved_ed():
    return ExclusionInterval(
        interval_id="ed-1",
        start_seconds=1200.0,
        end_seconds=1320.0,
        reason="ending_theme",
        source="chapter_marker",
        confidence="high",
        run="test-run",
        status="exclusion_approved",
    )


@pytest.fixture
def proposed_op():
    return ExclusionInterval(
        interval_id="op-prop-1",
        start_seconds=0.0,
        end_seconds=90.0,
        reason="opening_theme",
        source="low_confidence_classifier",
        confidence="low",
        run="test-run",
        status="exclusion_proposed",
    )


@pytest.fixture
def default_params():
    return SamplerParams(seed=42)


@pytest.fixture
def tmp_output(tmp_path):
    return tmp_path


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def generate_synthetic_video(
    output_path: str,
    shots: list,
    fps: int = 24,
    width: int = 320,
    height: int = 240,
) -> str:
    inputs = []
    filter_parts = []
    for i, (color, duration) in enumerate(shots):
        inputs.extend([
            "-f", "lavfi", "-i",
            f"color=c={color}:s={width}x{height}:d={duration}:r={fps}",
        ])
        filter_parts.append(f"[{i}:v]")
    n = len(shots)
    filter_complex = "".join(filter_parts) + f"concat=n={n}:v=1:a=0[out]"
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return output_path


@pytest.fixture
def synthetic_video_5shots(tmp_path):
    if not ffmpeg_available():
        pytest.skip("FFmpeg not available")
    path = str(tmp_path / "synth_5shots.mkv")
    return generate_synthetic_video(path, [
        ("red", 5.0),
        ("blue", 5.0),
        ("green", 5.0),
        ("white", 5.0),
        ("black", 5.0),
    ])


@pytest.fixture
def synthetic_video_long_with_changes(tmp_path):
    if not ffmpeg_available():
        pytest.skip("FFmpeg not available")
    path = str(tmp_path / "synth_long.mkv")
    return generate_synthetic_video(path, [
        ("red", 30.0),
        ("blue", 2.0),
        ("red", 10.0),
        ("green", 2.0),
        ("red", 10.0),
    ])
