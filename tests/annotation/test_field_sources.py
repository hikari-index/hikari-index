"""Each label field names the signal that answered it (for the accuracy report)."""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation.palette_labels import from_descriptor
from annotation.proposal import CandidateEvidence, build_labels, field_sources
from annotation.wd_labels import from_predictions, load

ALLOWLIST = load()
WARM = [0.3, 0.4, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0]


def _palette(luma_mean=0.5, p05=0.2, p95=0.8):
    return from_descriptor({
        "hue_family": {"bands": WARM,
                       "band_centers_deg": [0, 30, 60, 120, 180, 220, 275, 320]},
        "chroma": {"mean_sat_weighted": 0.45},
        "luma": {"mean": luma_mean, "p05": p05, "p50": luma_mean, "p95": p95},
    })


def _evidence(tags=None, faces=None, fraction=None, boxes=(), **extra):
    return CandidateEvidence(
        candidate_id="cand-0001", shot_id="shot-001", palette=_palette(),
        wd=from_predictions(tags, ALLOWLIST) if tags is not None else None,
        face_count=faces, face_fraction=fraction, faces=boxes,
        frame_width=1920, **extra)


FLAT = {
    "setting": ("setting_time_weather", "setting"),
    "time": ("setting_time_weather", "time"),
    "weather": ("setting_time_weather", "weather"),
    "angle": ("angle_composition", "angle"),
    "composition": ("angle_composition", "composition"),
    "lighting": ("lighting_color_character", "lighting"),
    "color_bias": ("lighting_color_character", "color_bias"),
    "saturation": ("lighting_color_character", "saturation"),
    "shot_scale": ("shot_scale",),
    "people": ("people",),
}


def _value(labels, path):
    for key in path:
        labels = labels[key]
    return labels


def test_a_source_is_named_exactly_when_a_value_is():
    tag_sets = [None, {}, {"indoors": 0.9, "night": 0.8, "1girl": 0.9},
                {"outdoors": 0.9, "rain": 0.8, "from_above": 0.7, "backlighting": 0.6},
                {"scenery": 0.9, "no_humans": 0.9}, {"upper_body": 0.8, "2boys": 0.7}]
    face_sets = [(None, None, ()), (0, None, ()),
                 (1, 0.2, ({"box": [900, 100, 1020, 260]},)),
                 (3, 0.05, ({"box": [100, 100, 200, 200]},))]
    for tags, (count, fraction, boxes), symmetrical, angle, entropy in itertools.product(
            tag_sets, face_sets, (False, True), (None, ("low", 0.9)), (5.0, 1.0)):
        evidence = _evidence(tags, count, fraction, boxes,
                             is_symmetrical=symmetrical, entropy=entropy,
                             camera_angle=angle)
        labels, sources = build_labels(evidence), field_sources(evidence)
        assert set(sources) == set(FLAT)
        for name, path in FLAT.items():
            value = _value(labels, path)
            assert (sources[name]["source"] == "none") == (value == "abstain"), (
                name, value, sources[name], tags, count)


def test_people_names_the_signal_that_decided():
    assert field_sources(_evidence({"1girl": 0.9}))["people"]["source"] == "tagger"
    assert field_sources(_evidence({"1girl": 0.9}, faces=1))["people"]["source"] == "tagger+faces"
    # Three faces raise the tagger's one: the face count decided.
    assert field_sources(_evidence({"1girl": 0.9}, faces=3))["people"]["source"] == "faces"
    # No face found for the tagger's one: the tagger's value stands alone.
    assert field_sources(_evidence({"1girl": 0.9}, faces=0))["people"]["source"] == "tagger"
    assert field_sources(_evidence(None, faces=2))["people"]["source"] == "faces"


def test_shot_scale_and_composition_carry_their_lane():
    sources = field_sources(_evidence({}, faces=1, fraction=0.2,
                                      boxes=({"box": [900, 100, 1020, 260]},)))
    assert sources["shot_scale"]["source"] == "face-occupancy"
    assert sources["composition"]["source"] == "face-position"
    assert field_sources(_evidence({}, is_symmetrical=True))["composition"]["source"] == "mirror"


def test_weather_from_an_interior_is_marked_as_inferred():
    sources = field_sources(_evidence({"indoors": 0.9}))
    assert sources["weather"]["source"] == "interior-rule"
    assert sources["setting"]["source"] == "tagger"


def test_lighting_names_the_tagger_only_where_it_spoke():
    assert field_sources(_evidence({"backlighting": 0.8}))["lighting"]["source"] == "tagger"
    dark = CandidateEvidence(candidate_id="cand-0001", shot_id="shot-001",
                             palette=_palette(luma_mean=0.13, p95=0.26), wd=None)
    assert field_sources(dark)["lighting"]["source"] == "palette"


def test_angle_comes_from_the_classifier_when_it_ran():
    tagged = {"from_above": 0.7}
    assert build_labels(_evidence(tagged))["angle_composition"]["angle"] == "high"
    assert field_sources(_evidence(tagged))["angle"]["source"] == "tagger"
    evidence = _evidence(tagged, camera_angle=("low", 0.93), entropy=5.0)
    assert build_labels(evidence)["angle_composition"]["angle"] == "low"
    assert field_sources(evidence)["angle"] == {"source": "angle-classifier", "score": 0.93}
    # Without the tagger the classifier still answers.
    assert build_labels(_evidence(None, camera_angle=("dutch", 0.6)))["angle_composition"]["angle"] == "dutch"


def test_angle_abstains_on_a_text_only_card():
    # Text on a flat ground is a card: no camera to name an angle for.
    card = _evidence({"english_text": 0.9}, camera_angle=("eye-level", 0.99), entropy=1.0)
    assert build_labels(card)["angle_composition"]["angle"] == "abstain"
    assert field_sources(card)["angle"]["source"] == "none"
    # Titles over a picture keep it.
    titled = _evidence({"english_text": 0.9}, camera_angle=("low", 0.8), entropy=5.0)
    assert build_labels(titled)["angle_composition"]["angle"] == "low"
