"""A run that detected nothing must not call itself complete.

OpenCV read zero frames from an AV1 source without raising. Scene detection
returned one shot for a 28-minute episode, the sampler produced three
candidates, extraction succeeded through a different decoder, and the run
reported `state: complete` with two published frames. Nothing in the output
said the source had never been decoded.

That is the failure mode worth guarding: not a crash, but a result that looks
like data.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from frame_sample.sampler import (
    MIN_DURATION_FOR_DETECTION_CHECK,
    MIN_SHOTS_PER_MINUTE,
    _assert_detection_plausible,
)
from frame_sample.scene_score import SceneBoundary


def _boundaries(count: int) -> list[SceneBoundary]:
    return [SceneBoundary(timestamp=float(i + 1), score=50.0) for i in range(count)]


def test_the_av1_case_is_rejected():
    """28 minutes, one boundary: what the AV1 run actually produced."""
    with pytest.raises(RuntimeError, match="decode failure"):
        _assert_detection_plausible("x.mkv", 1680.0, _boundaries(1))


def test_zero_boundaries_on_a_long_source_is_rejected():
    with pytest.raises(RuntimeError, match="scene detection found 0"):
        _assert_detection_plausible("x.mkv", 1680.0, [])


def test_a_real_episode_passes():
    """Measured density ran 12.2 to 25.8 shots per minute across five episodes."""
    _assert_detection_plausible("x.mkv", 1440.0, _boundaries(326))


def test_the_floor_is_far_below_anything_observed():
    """A 24-minute episode needs only 24 boundaries to pass, against 326 real."""
    minutes = 1440.0 / 60.0
    _assert_detection_plausible("x.mkv", 1440.0, _boundaries(int(minutes * MIN_SHOTS_PER_MINUTE)))


def test_a_short_source_is_exempt():
    """A clip may legitimately be one continuous take."""
    _assert_detection_plausible("x.mkv", MIN_DURATION_FOR_DETECTION_CHECK - 1, [])


def test_the_message_names_the_source_and_the_counts():
    with pytest.raises(RuntimeError) as error:
        _assert_detection_plausible("/source/thing.mkv", 1680.0, _boundaries(1))
    message = str(error.value)
    assert "/source/thing.mkv" in message
    assert "1 boundaries" in message
    assert "backend" in message
