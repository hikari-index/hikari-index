"""Integration tests: full pipeline with synthetic video + byte-level determinism."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers" / "frame_sample"
sys.path.insert(0, str(PROVIDER_ROOT.parent))

from frame_sample.exclusion import ExclusionInterval
from frame_sample.sampler import FrameSampler, SamplerParams

from conftest import ffmpeg_available, generate_synthetic_video


pytestmark = pytest.mark.skipif(
    not ffmpeg_available(),
    reason="FFmpeg not available",
)


class TestFullPipeline:
    def test_basic_run_produces_candidates(self, synthetic_video_5shots):
        sampler = FrameSampler()
        result = sampler.run(synthetic_video_5shots)
        assert len(result.candidates) > 0

    def test_fingerprinting_is_off_unless_asked(self, synthetic_video_5shots):
        """Hashing the input on every run was a full extra read of the source
        on top of the decode, and a second reason to wake a spun-down disk."""
        result = FrameSampler().run(synthetic_video_5shots)
        assert result.source_fingerprint == ""
        assert "source_fingerprint" not in result.to_dict()

    def test_fingerprint_targets_the_named_path(self, synthetic_video_5shots):
        """The path hashed is explicit, so a passage run can fingerprint the
        real source rather than the stream-copied slice it is working on."""
        result = FrameSampler().run(
            synthetic_video_5shots, fingerprint_path=synthetic_video_5shots
        )
        assert len(result.source_fingerprint) == 64
        assert result.to_dict()["source_fingerprint"] == result.source_fingerprint

    def test_run_with_exclusions(self, synthetic_video_5shots):
        op = ExclusionInterval(
            interval_id="op-1", start_seconds=0.0, end_seconds=5.0,
            reason="opening_theme", source="chapter_marker",
            confidence="high", run="test", status="exclusion_approved",
        )
        sampler = FrameSampler()
        result = sampler.run(synthetic_video_5shots, exclusions=[op])
        for c in result.candidates:
            assert c.exclusion_status in ("cleared", "held_unresolved")
        cleared = [c for c in result.candidates if c.exclusion_status == "cleared"]
        for c in cleared:
            assert c.timestamp_seconds > 5.0

    def test_shot_ids_are_detected_shot_records(self, synthetic_video_5shots):
        sampler = FrameSampler()
        result = sampler.run(synthetic_video_5shots)
        for c in result.candidates:
            assert c.shot_id.startswith("shot-"), (
                f"shot_id should be a detected-shot record (shot-NNN), got {c.shot_id}"
            )

    def test_scene_score_provenance_present(self, synthetic_video_5shots):
        sampler = FrameSampler()
        result = sampler.run(synthetic_video_5shots)
        assert result.scene_score_provenance is not None
        assert result.scene_score_provenance["detector"] == "pyscenedetect-adaptive"
        assert result.scene_score_provenance["content_metric"] == "content_val"


class TestDeterminism:
    def test_byte_level_determinism(self, synthetic_video_5shots):
        sampler1 = FrameSampler(seed=42)
        result1 = sampler1.run(synthetic_video_5shots)
        json1 = result1.to_json(created_at="2026-07-20T00:00:00Z")

        sampler2 = FrameSampler(seed=42)
        result2 = sampler2.run(synthetic_video_5shots)
        json2 = result2.to_json(created_at="2026-07-20T00:00:00Z")

        assert json1 == json2, "byte-level determinism failed: outputs differ"

    def test_different_seeds_different_output(self, synthetic_video_5shots):
        sampler1 = FrameSampler(seed=42)
        result1 = sampler1.run(synthetic_video_5shots)

        sampler2 = FrameSampler(seed=99)
        result2 = sampler2.run(synthetic_video_5shots)

        ts1 = [c.timestamp_seconds for c in result1.candidates]
        ts2 = [c.timestamp_seconds for c in result2.candidates]
        assert ts1 != ts2, "different seeds should produce different candidates"

    def test_determinism_with_exclusions(self, synthetic_video_5shots):
        op = ExclusionInterval(
            interval_id="op-1", start_seconds=0.0, end_seconds=3.0,
            reason="opening_theme", source="chapter_marker",
            confidence="high", run="test", status="exclusion_approved",
        )
        proposed = ExclusionInterval(
            interval_id="prop-1", start_seconds=10.0, end_seconds=15.0,
            reason="other", source="low_confidence_classifier",
            confidence="low", run="test", status="exclusion_proposed",
        )
        exclusions = [op, proposed]

        sampler1 = FrameSampler(seed=42)
        r1 = sampler1.run(synthetic_video_5shots, exclusions=exclusions)
        j1 = r1.to_json(created_at="2026-07-20T00:00:00Z")

        sampler2 = FrameSampler(seed=42)
        r2 = sampler2.run(synthetic_video_5shots, exclusions=exclusions)
        j2 = r2.to_json(created_at="2026-07-20T00:00:00Z")

        assert j1 == j2


class TestSceneDetectionRegression:
    def test_scene_detection_affects_default_output(self):
        from frame_sample.sampler import select_candidates, SamplerParams
        from frame_sample.scene_score import SceneBoundary

        duration = 60.0
        params = SamplerParams()

        boundaries_none = []
        boundaries_with_scenes = [
            SceneBoundary(timestamp=15.0, score=40.0),
            SceneBoundary(timestamp=30.0, score=50.0),
            SceneBoundary(timestamp=45.0, score=35.0),
        ]

        candidates_none, _ = select_candidates(duration, boundaries_none, [], params)
        candidates_with, _ = select_candidates(duration, boundaries_with_scenes, [], params)

        ts_none = [c.timestamp_seconds for c in candidates_none]
        ts_with = [c.timestamp_seconds for c in candidates_with]

        assert ts_none != ts_with, (
            "REGRESSION: scene detection has no effect on default output! "
            f"Without boundaries: {len(candidates_none)} candidates, "
            f"With boundaries: {len(candidates_with)} candidates"
        )

        shot_ids_none = set(c.shot_id for c in candidates_none)
        shot_ids_with = set(c.shot_id for c in candidates_with)
        assert shot_ids_none != shot_ids_with, (
            "REGRESSION: shot_ids are identical with and without boundaries"
        )

    def test_at_least_one_candidate_has_real_scene_score(self):
        from frame_sample.sampler import select_candidates, SamplerParams
        from frame_sample.scene_score import SceneBoundary

        duration = 60.0
        params = SamplerParams()

        boundaries = [
            SceneBoundary(timestamp=15.0, score=40.0),
            SceneBoundary(timestamp=30.0, score=50.0),
        ]

        candidates, _ = select_candidates(duration, boundaries, [], params)

        scored = [c for c in candidates if c.scene_score > 0]
        assert len(scored) > 0, (
            "REGRESSION: no candidate has scene_score > 0 under default params! "
            "Scene detection is not being used."
        )

        max_score = max(c.scene_score for c in candidates)
        assert max_score > 1.0, (
            f"REGRESSION: max scene_score is {max_score}, expected real measured values"
        )
