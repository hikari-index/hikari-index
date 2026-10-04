"""The video track's stated length, read by the real ffprobe from synthetic files.

The picture-end check measures against it instead of the container's
duration, which a Matroska segment can set past every track (a release
declared its last chapter's end, 9.7 s after the last frame, and a whole
episode was refused as damaged).
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "providers"))

from conftest import ffmpeg_available
from frame_sample.pipeline_cli import _video_stated_seconds
from frame_sample.scene_score import probe_video

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg not available")

# Matroska Segment Info > Duration: element id 0x4489, an 8-byte float in
# TimestampScale units (milliseconds as ffmpeg writes it).
_SEGMENT_DURATION = b"\x44\x89\x88"


def _encode(path: Path, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc2=s=160x90:r=24:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _declare_segment_seconds(path: Path, seconds: float) -> None:
    """Rewrite the segment's declared duration, as a muxer that writes the
    last chapter's end there does. The tracks are untouched."""
    data = bytearray(path.read_bytes())
    at = data.find(_SEGMENT_DURATION)
    assert at >= 0, "no segment duration element"
    data[at + 3:at + 11] = struct.pack(">d", seconds * 1000.0)
    path.write_bytes(bytes(data))


def test_a_segment_declaring_past_its_tracks(tmp_path):
    source = tmp_path / "long-last-chapter.mkv"
    _encode(source, 4.0)
    _declare_segment_seconds(source, 13.66)

    assert probe_video(str(source))["duration"] == pytest.approx(13.66, abs=0.01)
    assert _video_stated_seconds(str(source), "ffprobe") == pytest.approx(4.0, abs=0.1)


def test_a_container_with_a_numeric_stream_duration(tmp_path):
    source = tmp_path / "plain.mp4"
    _encode(source, 3.0)

    assert _video_stated_seconds(str(source), "ffprobe") == pytest.approx(3.0, abs=0.1)
