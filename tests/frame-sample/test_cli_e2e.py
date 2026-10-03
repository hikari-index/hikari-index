"""End-to-end coverage of the shipped CLI entrypoint.

These tests exercise `providers/frame_sample/cli.py` the way a deployment runs
it: the real `python -m frame_sample.cli` process, default config, a synthetic
video, and validation of the bytes actually written to disk. The earlier
defects (a dead scene branch, then a `SamplerParams(TypeError)` crash) survived
a green suite precisely because nothing ran the shipped entrypoint under its
defaults. Do not replace the subprocess invocation with an in-process call that
skips argv parsing and the process exit path.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROVIDERS_DIR = REPO_ROOT / "providers"
sys.path.insert(0, str(PROVIDERS_DIR))

from conftest import ffmpeg_available, generate_synthetic_video

SHOT_ID_RE = re.compile(r"^shot-\d{3}$")
CANDIDATE_ID_RE = re.compile(r"^cand-\d{4}$")
FIXED_CREATED_AT = "2026-07-20T00:00:00Z"


pytestmark = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="FFmpeg not available",
)


def _run_cli(args, extra_env=None):
    """Invoke the shipped entrypoint as a real subprocess.

    PYTHONPATH must carry the providers dir so the child process resolves
    `frame_sample` without relying on the parent's sys.path edits.
    """
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        f"{PROVIDERS_DIR}{os.pathsep}{existing}" if existing else str(PROVIDERS_DIR)
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "frame_sample.cli", *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture
def synth_source(tmp_path):
    """A synthetic clip with hard colour cuts, so AdaptiveDetector fires."""
    path = str(tmp_path / "synth_cli.mkv")
    return generate_synthetic_video(path, [
        ("red", 5.0),
        ("blue", 5.0),
        ("green", 5.0),
        ("white", 5.0),
        ("black", 5.0),
    ])


class TestShippedEntrypoint:
    def test_cli_runs_and_writes_valid_output(self, synth_source, tmp_path):
        out_path = tmp_path / "candidates.json"
        proc = _run_cli([
            "--input", synth_source,
            "--output", str(out_path),
            "--created-at", FIXED_CREATED_AT,
        ])

        # (1) exit 0 — the TypeError regression would surface here.
        assert proc.returncode == 0, (
            f"CLI exited {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
        assert out_path.exists(), "CLI did not write the output file"

        doc = json.loads(out_path.read_text(encoding="utf-8"))

        # (2) structural invariants of the bytes actually written.
        assert doc["schema_version"] == "1.2"
        assert doc["provider"] == "frame-sample"
        assert doc["created_at"] == FIXED_CREATED_AT
        candidates = doc["candidates"]
        assert len(candidates) > 0, "no candidates written"

        # (3) shot-NNN ids on every candidate.
        for c in candidates:
            assert SHOT_ID_RE.match(c["shot_id"]), (
                f"shot_id {c['shot_id']!r} is not a shot-NNN record"
            )
            assert CANDIDATE_ID_RE.match(c["candidate_id"]), c["candidate_id"]
            assert c["exclusion_status"] in ("cleared", "held_unresolved")

        # (4) at least one real, measured scene score — proves scene detection
        # actually drove selection under the default config.
        scored = [c for c in candidates if c["scene_score"] > 0]
        assert scored, "no candidate carries scene_score > 0 under default config"


    def test_adaptive_threshold_flag_accepted(self, synth_source, tmp_path):
        out_path = tmp_path / "candidates_at.json"
        proc = _run_cli([
            "--input", synth_source,
            "--output", str(out_path),
            "--adaptive-threshold", "3.0",
            "--created-at", FIXED_CREATED_AT,
        ])
        assert proc.returncode == 0, proc.stderr
        doc = json.loads(out_path.read_text(encoding="utf-8"))
        assert doc["parameters"]["adaptive_threshold"] == 3.0

    def test_removed_flags_are_rejected(self, synth_source, tmp_path):
        """The Step 1b-removed flags must not silently reappear."""
        out_path = tmp_path / "candidates_stale.json"
        for stale in ("--segment-duration", "--long-segment-threshold"):
            proc = _run_cli([
                "--input", synth_source,
                "--output", str(out_path),
                stale, "10.0",
            ])
            assert proc.returncode != 0, f"stale flag {stale} was accepted"
            assert "unrecognized arguments" in proc.stderr


