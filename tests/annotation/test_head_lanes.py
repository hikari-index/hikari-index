"""The head lanes (#16): fill shot scale and composition where no face answered."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation.composition_labels import resolve as composition
from annotation.palette_labels import from_descriptor
from annotation.proposal import CandidateEvidence, build_labels, field_sources
from annotation.shot_scale import from_head_height, resolve
from annotation.wd_labels import from_predictions, load
from inference.detect_figures import detect

ALLOWLIST = load()


@pytest.mark.parametrize("height,expected", [
    (0.95, "close-up"), (0.70, "close-up"), (0.45, "medium"), (0.20, "medium"), (0.10, "wide"),
])
def test_head_height_bands(height, expected):
    verdict = from_head_height(height)
    assert (verdict.value, verdict.lane) == (expected, "head-height")


def test_the_head_lane_only_fills_what_every_other_lane_left_empty():
    assert resolve("medium", 0.6, head_height=0.9).lane == "tagger"
    assert resolve("abstain", 0.0, face_fraction=0.05, head_height=0.9).lane == "face-occupancy"
    assert resolve("abstain", 0.0, scenery_tagged=True, head_height=0.9).lane == "scenery"
    filled = resolve("abstain", 0.0, head_height=0.9)
    assert (filled.value, filled.lane) == ("close-up", "head-height")
    assert resolve("abstain", 0.0).lane == "none"


def test_a_head_near_a_cut_scores_lower():
    assert from_head_height(0.69).score < from_head_height(0.85).score


def _box(x0, x1):
    return {"box": [x0, 100, x1, 300]}


def test_composition_uses_a_head_only_where_no_face_was_found():
    face, head = [_box(900, 1020)], [_box(100, 200)]
    by_face = composition(faces=face, heads=head, frame_width=1920)
    assert (by_face.value, by_face.basis) == ("centered", "face-position")
    by_head = composition(faces=(), heads=[_box(590, 690)], frame_width=1920)
    assert (by_head.value, by_head.basis) == ("rule-of-thirds", "head-position")
    assert by_head.score < by_face.score
    assert composition(faces=(), heads=(), frame_width=1920).basis == "no-subject-signal"


def test_a_text_card_still_abstains_with_a_head_on_it():
    verdict = composition(text_present=True, entropy=1.0, heads=[_box(900, 1020)], frame_width=1920)
    assert verdict.value == "abstain"


def test_fusion_names_the_head_lanes_as_sources():
    evidence = CandidateEvidence(
        candidate_id="cand-0001", shot_id="shot-001", palette=from_descriptor(None),
        wd=from_predictions({"1girl": 0.9}, ALLOWLIST), face_count=0,
        heads=({"box": [590, 100, 690, 900], "height_fraction": 0.74},), head_height=0.74,
        frame_width=1920)
    labels, sources = build_labels(evidence), field_sources(evidence)
    assert labels["shot_scale"] == "close-up"
    assert sources["shot_scale"]["source"] == "head-height"
    assert labels["angle_composition"]["composition"] == "rule-of-thirds"
    assert sources["composition"]["source"] == "head-position"


class _FakeSession:
    """Stands in for an onnxruntime session: one class, rows cx, cy, w, h,
    score over three anchors, in the resized image's pixels."""

    def __init__(self, rows):
        self.rows = np.array(rows, dtype=np.float32)
        self.seen = None

    def get_inputs(self):
        return [type("Input", (), {"name": "images"})()]

    def run(self, _outputs, feeds):
        self.seen = feeds["images"].shape
        return [self.rows[None]]


def test_detect_resizes_like_imgutils_and_maps_boxes_back():
    # 1920x1080 -> 640x360, rounded up to multiples of 32: 640x384.
    session = _FakeSession([
        [320.0, 320.0, 100.0],      # cx
        [192.0, 192.0, 50.0],       # cy
        [64.0, 60.0, 20.0],         # w
        [96.0, 90.0, 20.0],         # h
        [0.9, 0.8, 0.2],            # score: the second overlaps the first; the third is below the cut
    ])
    found = detect(session, Image.new("RGB", (1920, 1080)), score_cut=0.4, iou=0.5)
    assert session.seen == (1, 3, 384, 640)
    assert len(found) == 1
    x0, y0, x1, y1 = found[0]["box"]
    assert (round(x0), round(x1)) == (864, 1056)        # 288..352 scaled by 3
    assert (round(y0), round(y1)) == (405, 675)         # 144..240 scaled by 1080/384
    assert found[0]["height_fraction"] == pytest.approx(0.25)
