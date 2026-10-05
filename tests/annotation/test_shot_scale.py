"""The shot-scale cascade: three lanes, tried in order, abstaining cleanly.

Face size alone cannot answer a frame with no face, and 38-54% of published
frames on two real episodes have none. The cascade exists so that no single
signal is load-bearing and so each lane can be scored separately on the gold
set.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation.proposal import CandidateEvidence, build_labels, shot_scale_lane
from annotation.palette_labels import from_descriptor
from annotation.shot_scale import (FACE_CUTS, from_face_occupancy, from_scenery,
                                   resolve)


def _evidence(**kwargs) -> CandidateEvidence:
    return CandidateEvidence(
        candidate_id="cand-0001", shot_id="shot-001",
        palette=from_descriptor(None), **kwargs,
    )


# --- the lanes in isolation ------------------------------------------------

@pytest.mark.parametrize("fraction,expected", [
    (0.60, "close-up"),
    (0.38, "close-up"),
    (0.052, "medium"),
    (0.0015, "wide"),
    (0.0002, "wide"),
])
def test_face_occupancy_orders_the_scales(fraction, expected):
    assert from_face_occupancy(fraction).value == expected


def test_the_face_lane_never_asserts_the_extreme_classes():
    """Calibrated on the reviewed gold sample: human extreme-close-ups and
    close-ups overlap in fraction (0.4731 ECU vs 0.5315 CU), so no cut
    separates them. 5 of 7 old extreme-close-up calls were corrected. No
    other lane asserts the extremes either (see FACE_CUTS)."""
    for fraction in (0.45, 0.60, 0.95, 0.0007, 0.0001):
        assert from_face_occupancy(fraction).value not in (
            "extreme-close-up", "extreme-wide")


def test_face_occupancy_is_monotonic():
    """A bigger face never means a wider shot."""
    order = ["wide", "medium", "close-up"]
    fractions = [0.0001, 0.001, 0.01, 0.05, 0.2, 0.5, 0.9]
    seen = [order.index(from_face_occupancy(f).value) for f in fractions]
    assert seen == sorted(seen)


def test_a_face_near_a_cut_scores_lower_than_one_in_the_middle():
    cut = FACE_CUTS[1][0]  # the medium/wide boundary
    assert from_face_occupancy(cut * 1.01).score < from_face_occupancy(0.052).score


def test_no_face_means_the_face_lane_says_nothing():
    assert from_face_occupancy(0.0).value == "abstain"
    assert from_face_occupancy(0.0).lane == "none"


def test_scenery_only_fires_on_the_scenery_tag():
    assert from_scenery(True).value == "wide"
    assert from_scenery(False).value == "abstain"


def test_scenery_scores_below_the_face_lane():
    """It infers scale from subject matter, which is the weakest evidence."""
    assert from_scenery(True).score < from_face_occupancy(0.052).score


# --- the cascade -----------------------------------------------------------

def test_the_tagger_wins_when_it_named_a_scale():
    got = resolve("close-up", 0.8, face_fraction=0.0012, scenery_tagged=True)
    assert (got.value, got.lane) == ("close-up", "tagger")


def test_face_occupancy_takes_over_when_the_tagger_abstained():
    got = resolve("abstain", 0.0, face_fraction=0.052, scenery_tagged=True)
    assert (got.value, got.lane) == ("medium", "face-occupancy")


def test_scenery_is_the_last_resort():
    got = resolve("abstain", 0.0, face_fraction=None, scenery_tagged=True)
    assert (got.value, got.lane) == ("wide", "scenery")


def test_a_frame_no_lane_reaches_abstains_rather_than_guessing():
    """People shot from behind: a person is there, no face, no scenery."""
    got = resolve("abstain", 0.0, face_fraction=0.0, scenery_tagged=False)
    assert got.value == "abstain"
    assert got.score == 0.0
    assert got.lane == "none"


# --- integration through the proposal ---------------------------------------

def test_the_proposal_carries_the_face_lane_verdict():
    labels = build_labels(_evidence(face_count=1, face_fraction=0.30))
    assert labels["shot_scale"] == "close-up"


def test_the_lane_is_reported_for_calibration():
    assert shot_scale_lane(_evidence(face_count=1, face_fraction=0.30)) == "face-occupancy"
    assert shot_scale_lane(_evidence(scenery_tagged=True)) == "scenery"
    assert shot_scale_lane(_evidence()) == "none"


def test_a_face_free_frame_still_abstains_end_to_end():
    """The whole point of the objection: faces must not become load-bearing."""
    labels = build_labels(_evidence(face_count=0, face_fraction=0.0))
    assert labels["shot_scale"] == "abstain"


def test_an_abstaining_scale_scores_zero_not_a_low_confidence():
    from annotation.proposal import build_scores

    scores = {e["family"]: e for e in build_scores(_evidence())}
    assert scores["shot-scale"]["label"] == "abstain"
    assert scores["shot-scale"]["score"] == 0.0
