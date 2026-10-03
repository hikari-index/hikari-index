"""Tests for candidate selection, exclusion, and safety-margin merge."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers" / "frame_sample"
sys.path.insert(0, str(PROVIDER_ROOT.parent))

from frame_sample.exclusion import (
    ExclusionInterval,
    apply_exclusions,
    merge_safety_margins,
)
from frame_sample.sampler import SamplerParams, samples_for_shot, select_candidates
from frame_sample.scene_score import SceneBoundary


def _ts_list(candidates):
    return [c.timestamp_seconds for c in candidates]


class TestDensitySampling:
    @pytest.mark.parametrize("density,expected_min", [
        (0.25, 1),
        (0.50, 2),
        (0.75, 3),
    ])
    def test_density_produces_candidates(self, density, expected_min):
        duration = 100.0
        boundaries = []
        exclusions = []
        params = SamplerParams(
            density=density,
            boundary_margin_seconds=0.5,
            seed=42,
        )
        candidates, held = select_candidates(duration, boundaries, exclusions, params)
        assert len(candidates) >= expected_min, (
            f"density={density}: got {len(candidates)} candidates, expected >= {expected_min}"
        )
        for c in candidates:
            assert c.exclusion_status == "cleared"

    def test_boundary_margin_enforced(self):
        duration = 30.0
        boundaries = [
            SceneBoundary(timestamp=10.0, score=5.0),
            SceneBoundary(timestamp=20.0, score=5.0),
        ]
        exclusions = []
        params = SamplerParams(
            density=0.5,
            boundary_margin_seconds=1.0,
            seed=42,
        )
        candidates, _ = select_candidates(duration, boundaries, exclusions, params)
        shot_edges = [10.0, 20.0]
        margin = params.boundary_margin_seconds
        for c in candidates:
            for edge in shot_edges:
                dist = abs(c.timestamp_seconds - edge)
                assert dist >= margin * 0.5, (
                    f"candidate {c.timestamp_seconds} too close to shot edge {edge} "
                    f"(dist={dist}, margin={margin})"
                )


class TestDurationScaledSampling:
    """A flat per-shot count crammed samples into short cuts and made twins."""

    @pytest.mark.parametrize("sample_range,expected", [
        (0.4, 1),    # a sub-second cut earns exactly one frame
        (1.9, 1),
        (2.0, 2),
        (5.0, 3),
        (60.0, 3),   # capped, however long the take runs
    ])
    def test_sample_count_follows_shot_length(self, sample_range, expected):
        params = SamplerParams(min_sample_spacing_seconds=2.0, max_samples_per_shot=3)
        assert samples_for_shot(sample_range, params) == expected

    def test_density_scales_the_derived_count(self):
        params = SamplerParams(density=0.25, min_sample_spacing_seconds=2.0)
        assert samples_for_shot(5.0, params) == 1

    def test_short_shots_never_yield_near_simultaneous_samples(self):
        """The measured defect: three samples 80-200 ms apart inside one cut."""
        duration = 40.0
        boundaries = [
            SceneBoundary(timestamp=t, score=100.0)
            for t in (10.0, 11.5, 13.0, 14.5, 16.0)
        ]
        params = SamplerParams(boundary_margin_seconds=0.5, seed=42)
        candidates, _ = select_candidates(duration, boundaries, [], params)

        by_shot = {}
        for c in candidates:
            by_shot.setdefault(c.shot_id, []).append(c.timestamp_seconds)
        for shot_id, timestamps in by_shot.items():
            if len(timestamps) < 2:
                continue
            gaps = [b - a for a, b in zip(sorted(timestamps), sorted(timestamps)[1:])]
            assert min(gaps) > 0.5, (
                f"{shot_id} sampled {len(timestamps)} frames with a {min(gaps):.3f}s gap"
            )

    def test_extras_are_off_by_default(self):
        """Real scene scores are 70-124 against a threshold of 3.0, so the
        extra-per-boundary sample fired on every shot and added a third twin."""
        assert SamplerParams().extra_per_boundary == 0


class TestSceneScoreExtras:
    def test_high_score_shot_gets_extras(self):
        duration = 60.0
        boundaries = [
            SceneBoundary(timestamp=15.0, score=10.0),
            SceneBoundary(timestamp=35.0, score=10.0),
        ]
        exclusions = []
        params_with_extras = SamplerParams(
            density=0.5,
            boundary_margin_seconds=0.5,
            adaptive_threshold=3.0,
            extra_per_boundary=1,
            seed=42,
        )
        params_no_extras = SamplerParams(
            density=0.5,
            boundary_margin_seconds=0.5,
            adaptive_threshold=3.0,
            extra_per_boundary=0,
            seed=42,
        )
        cands_with, _ = select_candidates(duration, boundaries, exclusions, params_with_extras)
        cands_without, _ = select_candidates(duration, boundaries, exclusions, params_no_extras)
        assert len(cands_with) > len(cands_without), (
            f"high-score shots with extras: {len(cands_with)} vs no-extras: {len(cands_without)}"
        )
        scored = [c for c in cands_with if c.scene_score > 0]
        assert len(scored) >= 1, "shots with high scores should carry scene_score > 0"

    def test_low_score_shot_no_extras(self):
        duration = 30.0
        boundaries = [SceneBoundary(timestamp=15.0, score=1.0)]
        exclusions = []
        params = SamplerParams(
            density=0.5,
            adaptive_threshold=3.0,
            extra_per_boundary=1,
            seed=42,
        )
        candidates, _ = select_candidates(duration, boundaries, exclusions, params)
        for c in candidates:
            assert c.scene_score <= params.adaptive_threshold, (
                f"low-score shot should not trigger extras, got score={c.scene_score}"
            )


class TestExclusion:
    def test_approved_exclusion_removes_candidates(self, approved_op):
        duration = 120.0
        boundaries = []
        params = SamplerParams(density=0.5, safety_margin_seconds=0.0, seed=42)
        candidates, held = select_candidates(
            duration, boundaries, [approved_op], params
        )
        for c in candidates:
            assert c.timestamp_seconds > approved_op.end_seconds, (
                f"candidate {c.timestamp_seconds} inside approved exclusion [0, 90]"
            )

    def test_proposed_exclusion_holds_not_removes(self, proposed_op):
        duration = 120.0
        boundaries = []
        params = SamplerParams(density=0.5, safety_margin_seconds=0.0, seed=42)
        candidates, held = select_candidates(
            duration, boundaries, [proposed_op], params
        )
        held_candidates = [c for c in candidates if c.exclusion_status == "held_unresolved"]
        assert len(held_candidates) > 0, "proposed exclusion should hold candidates"
        for c in held_candidates:
            assert proposed_op.start_seconds <= c.timestamp_seconds <= proposed_op.end_seconds

    def test_proposed_never_auto_approved(self, proposed_op):
        assert proposed_op.status == "exclusion_proposed"
        assert not proposed_op.is_approved
        duration = 120.0
        params = SamplerParams(density=0.5, safety_margin_seconds=0.0, seed=42)
        candidates, _ = select_candidates(duration, [], [proposed_op], params)
        for c in candidates:
            assert c.exclusion_status != "cleared" or (
                c.timestamp_seconds < proposed_op.start_seconds
                or c.timestamp_seconds > proposed_op.end_seconds
            )

    def test_partial_op_only(self):
        op = ExclusionInterval(
            interval_id="op-only",
            start_seconds=0.0,
            end_seconds=90.0,
            reason="opening_theme",
            source="chapter_marker",
            confidence="high",
            run="test",
            status="exclusion_approved",
        )
        duration = 1200.0
        params = SamplerParams(density=0.5, safety_margin_seconds=0.0, seed=42)
        candidates, _ = select_candidates(duration, [], [op], params)
        post_op = [c for c in candidates if c.timestamp_seconds > 90.0]
        assert len(post_op) > 0, "content after OP should have candidates"

    def test_partial_ed_only(self):
        ed = ExclusionInterval(
            interval_id="ed-only",
            start_seconds=1200.0,
            end_seconds=1320.0,
            reason="ending_theme",
            source="chapter_marker",
            confidence="high",
            run="test",
            status="exclusion_approved",
        )
        duration = 1320.0
        params = SamplerParams(density=0.5, safety_margin_seconds=0.0, seed=42)
        candidates, _ = select_candidates(duration, [], [ed], params)
        pre_ed = [c for c in candidates if c.timestamp_seconds < 1200.0]
        assert len(pre_ed) > 0, "content before ED should have candidates"


class TestSafetyMarginMerge:
    def test_overlapping_margins_merge(self):
        a = ExclusionInterval(
            interval_id="a", start_seconds=0.0, end_seconds=90.0,
            reason="opening_theme", source="chapter_marker",
            confidence="high", run="test", status="exclusion_approved",
        )
        b = ExclusionInterval(
            interval_id="b", start_seconds=91.0, end_seconds=100.0,
            reason="content_warning", source="human_confirmed",
            confidence="high", run="test", status="exclusion_approved",
        )
        merged = merge_safety_margins([a, b], margin_seconds=2.0)
        assert len(merged) == 1, f"overlapping margins should merge, got {len(merged)}"
        assert merged[0][0] == 0.0
        assert merged[0][1] == 102.0

    def test_disjoint_margins_stay_separate(self):
        a = ExclusionInterval(
            interval_id="a", start_seconds=0.0, end_seconds=90.0,
            reason="opening_theme", source="chapter_marker",
            confidence="high", run="test", status="exclusion_approved",
        )
        b = ExclusionInterval(
            interval_id="b", start_seconds=200.0, end_seconds=300.0,
            reason="ending_theme", source="chapter_marker",
            confidence="high", run="test", status="exclusion_approved",
        )
        merged = merge_safety_margins([a, b], margin_seconds=2.0)
        assert len(merged) == 2

    def test_candidates_in_merged_region_excluded(self):
        a = ExclusionInterval(
            interval_id="a", start_seconds=0.0, end_seconds=90.0,
            reason="opening_theme", source="chapter_marker",
            confidence="high", run="test", status="exclusion_approved",
        )
        b = ExclusionInterval(
            interval_id="b", start_seconds=91.0, end_seconds=100.0,
            reason="content_warning", source="human_confirmed",
            confidence="high", run="test", status="exclusion_approved",
        )
        duration = 150.0
        params = SamplerParams(density=0.5, safety_margin_seconds=2.0, seed=42)
        candidates, _ = select_candidates(duration, [], [a, b], params)
        for c in candidates:
            assert c.timestamp_seconds > 102.0 or c.timestamp_seconds < 0.0, (
                f"candidate {c.timestamp_seconds} inside merged exclusion region"
            )


class TestApplyExclusions:
    def test_approved_removes(self):
        iv = ExclusionInterval(
            interval_id="x", start_seconds=10.0, end_seconds=20.0,
            reason="other", source="human_confirmed",
            confidence="high", run="test", status="exclusion_approved",
        )
        timestamps = [5.0, 15.0, 25.0]
        kept, held = apply_exclusions(timestamps, [iv], safety_margin_seconds=0.0)
        assert 5.0 in kept
        assert 25.0 in kept
        assert 15.0 not in kept

    def test_proposed_holds(self):
        iv = ExclusionInterval(
            interval_id="x", start_seconds=10.0, end_seconds=20.0,
            reason="other", source="low_confidence_classifier",
            confidence="low", run="test", status="exclusion_proposed",
        )
        timestamps = [5.0, 15.0, 25.0]
        kept, held = apply_exclusions(timestamps, [iv], safety_margin_seconds=0.0)
        assert 5.0 in kept
        assert 25.0 in kept
        assert 15.0 not in kept
        held_ts = [t for t, _ in held]
        assert 15.0 in held_ts
