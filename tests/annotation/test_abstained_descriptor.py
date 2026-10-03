"""A colour provider that abstained must not take the whole run down.

One frame in a 200-frame episode had no usable palette colours, so the provider
wrote a short abstain record instead of a descriptor. The proposal emitter read
that file, saw it existed, and indexed into measurement blocks that were not
there -- a bare `KeyError: 'chroma'` that produced no output at all for the
episode. Presence on disk is not evidence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation.cli import emit
from annotation.palette_labels import from_descriptor, has_evidence

ABSTAINED = {
    "descriptor_schema": "hikari-color-descriptor/1",
    "provider": "palette-descriptor",
    "provider_version": "0.1.0",
    "reason": "no-usable-palette-colors",
    "status": "abstained",
}

MEASURED = {
    "descriptor_schema": "hikari-color-descriptor/1",
    "chroma": {"mean_sat_weighted": 0.42},
    "luma": {"mean": 0.2, "p05": 0.02, "p95": 0.6},
    "hue_family": {"bands": [0.5, 0.2, 0.0, 0.0, 0.1, 0.1, 0.1, 0.0]},
}


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.mark.parametrize("descriptor", [None, ABSTAINED, {"chroma": {}}])
def test_descriptors_without_measurements_carry_no_evidence(descriptor):
    assert has_evidence(descriptor) is False


def test_measured_descriptor_carries_evidence():
    assert has_evidence(MEASURED) is True


def test_abstained_descriptor_abstains_instead_of_raising():
    labels = from_descriptor(ABSTAINED)
    assert labels.palette_evidence == "abstain"
    assert labels.color_bias == "abstain"
    assert labels.lighting == "abstain"
    assert labels.color_bias_score == 0.0


def test_one_abstained_frame_does_not_lose_the_other_frames(tmp_path):
    bundle = tmp_path / "prepared"
    _write(bundle / "manifest.json", {
        "bundle_id": "b",
        "candidates": [
            {"candidate_id": "cand-0001", "shot_id": "shot-001"},
            {"candidate_id": "cand-0002", "shot_id": "shot-002"},
        ],
    })
    results = tmp_path / "results"
    _write(results / "artifacts" / "cand-0001.descriptor.json", MEASURED)
    _write(results / "artifacts" / "cand-0002.descriptor.json", ABSTAINED)

    doc = emit(bundle, results, "provider-fused", "config-default", "run-test")

    assert len(doc["proposals"]) == 2
    measured, abstained = doc["proposals"]
    assert measured["proposal"]["labels"]["lighting_color_character"][
        "palette_evidence"] == "available"
    assert abstained["proposal"]["labels"]["lighting_color_character"][
        "palette_evidence"] == "abstain"


def test_a_broken_candidate_names_itself(tmp_path):
    """A failure in one frame must say which frame, not just which key."""
    bundle = tmp_path / "prepared"
    _write(bundle / "manifest.json", {
        "bundle_id": "b",
        "candidates": [{"candidate_id": "cand-0009", "shot_id": "shot-001"}],
    })
    results = tmp_path / "results"
    results.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="cand-0009"):
        emit(bundle, results, "provider-fused", "config-default", "not a valid ref")
