"""Fusion of face boxes with tagger count hints."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation.people_labels import fuse
from annotation.wd_labels import from_predictions, load

ALLOWLIST = load()


def wd(**tags):
    return from_predictions(tags, ALLOWLIST)


class TestFaceIsAFloor:
    def test_faces_raise_an_undercounting_tagger(self):
        assert fuse(wd(**{"1girl": 0.9}), face_count=3).value == "small-group"

    def test_faces_never_lower_the_tagger(self):
        """Zero faces on back-turned or hooded figures is the detector's
        documented blind spot, not evidence that nobody is there."""
        assert fuse(wd(**{"2girls": 0.9}), face_count=0).value == "two"

    def test_faces_carry_the_field_when_the_tagger_abstains(self):
        assert fuse(wd(), face_count=2).value == "two"

    def test_tagger_carries_the_field_without_a_detector_run(self):
        assert fuse(wd(**{"2girls": 0.9}), face_count=None).value == "two"


class TestDisagreementSurfaces:
    def test_disagreement_is_flagged(self):
        verdict = fuse(wd(**{"2girls": 0.9}), face_count=0)
        assert verdict.evidence_inconsistent is True

    def test_agreement_is_not_flagged(self):
        verdict = fuse(wd(**{"2girls": 0.9}), face_count=2)
        assert verdict.evidence_inconsistent is False

    def test_disagreement_lowers_confidence_for_review(self):
        """The protocol lists inconsistent count evidence as a review trigger,
        so a split must not average into false confidence."""
        agree = fuse(wd(**{"2girls": 0.9}), face_count=2).score
        split = fuse(wd(**{"2girls": 0.9}), face_count=0).score
        assert split < agree

    def test_verdict_records_both_inputs(self):
        verdict = fuse(wd(**{"1girl": 0.9}), face_count=4)
        assert verdict.face_count == 4
        assert verdict.tagger_value == "one"
        assert verdict.value == "small-group"


class TestZero:
    def test_no_humans_with_no_faces_stays_zero(self):
        verdict = fuse(wd(**{"no_humans": 0.9}), face_count=0)
        assert verdict.value == "zero"
        assert verdict.evidence_inconsistent is False

    def test_a_face_overrides_no_humans(self):
        """A detected face is positive evidence; `no_humans` is an absence
        claim, and absence claims lose to positive ones."""
        verdict = fuse(wd(**{"no_humans": 0.9}), face_count=1)
        assert verdict.value == "one"
        assert verdict.evidence_inconsistent is True
