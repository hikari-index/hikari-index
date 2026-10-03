"""Bundle-verification tests for the inference embedder (no torch required)."""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from inference.embed_bundle import BundleValidationError, load_and_verify_bundle

# Smallest valid PNG (1x1 pixel); dimensions are irrelevant to verification.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def make_bundle(root: Path, schema_version: str = "2.2", bundle_id: str = "bundle-test-1"):
    candidates_dir = root / "candidates"
    candidates_dir.mkdir(parents=True)
    artifact_name = "candidates/c0001--00h00m01s000.png"
    (root / artifact_name).write_bytes(TINY_PNG)

    manifest = {
        "schema_version": schema_version,
        "bundle_id": bundle_id,
        "candidates": [
            {
                "candidate_id": "cand-0001",
                "artifact_name": artifact_name,
                "bytes": len(TINY_PNG),
                "sha256": hashlib.sha256(TINY_PNG).hexdigest(),
            }
        ],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    (root / "manifest.json").write_bytes(manifest_bytes)

    ready = {
        "bundle_id": bundle_id,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "bundle_digest": "sha256:synthetic",
        "state": "ready",
    }
    (root / "READY.json").write_text(json.dumps(ready, sort_keys=True))
    return manifest


def test_valid_bundle_passes(tmp_path):
    make_bundle(tmp_path)
    manifest, manifest_sha, ready = load_and_verify_bundle(tmp_path)
    assert manifest["bundle_id"] == "bundle-test-1"
    assert ready["manifest_sha256"] == manifest_sha
    assert len(manifest["candidates"]) == 1


def test_the_format_label_is_not_a_gate(tmp_path):
    """Old bundles and ones from a newer source worker both read: this
    stage needs each frame's id, name, size and checksum and nothing else."""
    for schema in ("2.1", "2.4", "9.9"):
        root = tmp_path / schema
        make_bundle(root, schema_version=schema)
        manifest, _, _ = load_and_verify_bundle(root)
        assert manifest["schema_version"] == schema


def test_a_bundle_without_what_this_stage_reads_says_so(tmp_path):
    make_bundle(tmp_path)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    del manifest["candidates"][0]["sha256"]
    body = json.dumps(manifest).encode()
    manifest_path.write_bytes(body)
    ready_path = tmp_path / "READY.json"
    ready = json.loads(ready_path.read_text())
    ready["manifest_sha256"] = hashlib.sha256(body).hexdigest()
    ready_path.write_text(json.dumps(ready))
    with pytest.raises(BundleValidationError, match="update"):
        load_and_verify_bundle(tmp_path)


def test_bundle_id_mismatch_rejected(tmp_path):
    make_bundle(tmp_path)
    with pytest.raises(BundleValidationError, match="bundle_id"):
        load_and_verify_bundle(tmp_path, expected_bundle_id="some-other-bundle")


def test_tampered_candidate_rejected(tmp_path):
    make_bundle(tmp_path)
    (tmp_path / "candidates" / "c0001--00h00m01s000.png").write_bytes(TINY_PNG + b"x")
    with pytest.raises(BundleValidationError, match="bytes/sha256"):
        load_and_verify_bundle(tmp_path)


def test_tampered_manifest_rejected(tmp_path):
    make_bundle(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    manifest["bundle_id"] = "bundle-renamed"
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    with pytest.raises(BundleValidationError, match="manifest sha256"):
        load_and_verify_bundle(tmp_path)


def test_missing_candidate_file_rejected(tmp_path):
    make_bundle(tmp_path)
    (tmp_path / "candidates" / "c0001--00h00m01s000.png").unlink()
    with pytest.raises(BundleValidationError, match="missing"):
        load_and_verify_bundle(tmp_path)


def _rewrite(root: Path, change):
    manifest = json.loads((root / "manifest.json").read_text())
    change(manifest)
    body = json.dumps(manifest, sort_keys=True).encode("utf-8")
    (root / "manifest.json").write_bytes(body)
    ready = json.loads((root / "READY.json").read_text())
    ready["manifest_sha256"] = hashlib.sha256(body).hexdigest()
    (root / "READY.json").write_text(json.dumps(ready))


def test_a_frame_outside_the_bundle_is_refused_even_with_a_matching_checksum(tmp_path):
    root = tmp_path / "bundle"
    make_bundle(root)
    (tmp_path / "outside.png").write_bytes(TINY_PNG)
    _rewrite(root, lambda m: m["candidates"][0].update(artifact_name="../outside.png"))
    with pytest.raises(BundleValidationError, match="outside the bundle"):
        load_and_verify_bundle(root)


@pytest.mark.parametrize("bad", ["../../escaped", "a/b", ".."])
def test_an_id_that_is_not_a_plain_name_is_refused(tmp_path, bad):
    make_bundle(tmp_path)
    _rewrite(tmp_path, lambda m: m["candidates"][0].update(candidate_id=bad))
    with pytest.raises(BundleValidationError, match="file name"):
        load_and_verify_bundle(tmp_path)
