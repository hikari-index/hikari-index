"""The CLI must actually consume the tagger and face results, not just palette."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation.cli import emit


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _bundle(tmp_path):
    bundle = tmp_path / "prepared"
    _write(bundle / "manifest.json", {
        "bundle_id": "b",
        "candidates": [{"candidate_id": "cand-0001", "shot_id": "shot-001"}],
    })
    results = tmp_path / "results"
    (results / "artifacts").mkdir(parents=True)
    return bundle, results


def test_without_tags_or_faces_the_families_abstain(tmp_path):
    bundle, results = _bundle(tmp_path)
    doc = emit(bundle, results, "provider-fused", "config-default", "run-test")
    labels = doc["proposals"][0]["proposal"]["labels"]
    assert labels["setting_time_weather"]["setting"] == "abstain"
    assert labels["people"] == "abstain"


def test_tags_are_consumed_when_supplied(tmp_path):
    bundle, results = _bundle(tmp_path)
    tags = tmp_path / "tags" / "result-manifest.json"
    _write(tags, {"candidates": [{
        "candidate_id": "cand-0001",
        "general_tags": {"outdoors": 0.9, "night": 0.85},
    }]})
    doc = emit(bundle, results, "provider-fused", "config-default", "run-test",
               tags=tags)
    stw = doc["proposals"][0]["proposal"]["labels"]["setting_time_weather"]
    assert stw["setting"] == "exterior"
    assert stw["time"] == "night"


def test_faces_fuse_with_tagger_counts(tmp_path):
    bundle, results = _bundle(tmp_path)
    tags = tmp_path / "tags" / "result-manifest.json"
    _write(tags, {"candidates": [{
        "candidate_id": "cand-0001", "general_tags": {"1girl": 0.9},
    }]})
    faces = tmp_path / "faces" / "result-manifest.json"
    _write(faces, {"candidates": [{"candidate_id": "cand-0001", "face_count": 3}]})
    doc = emit(bundle, results, "provider-fused", "config-default", "run-test",
               tags=tags, faces=faces)
    # 1girl says one, three faces raise it: faces are a floor.
    assert doc["proposals"][0]["proposal"]["labels"]["people"] == "small-group"


def test_a_missing_result_file_is_not_fatal(tmp_path):
    bundle, results = _bundle(tmp_path)
    doc = emit(bundle, results, "provider-fused", "config-default", "run-test",
               tags=tmp_path / "absent.json")
    assert doc["proposals"][0]["proposal"]["labels"]["setting_time_weather"]["setting"] == "abstain"


def test_the_run_records_the_allowlist_version(tmp_path):
    from annotation.wd_labels import load
    bundle, results = _bundle(tmp_path)
    doc = emit(bundle, results, "provider-fused", "config-default", "run-test")
    assert doc["allowlist_version"] == load().version
