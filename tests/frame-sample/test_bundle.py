"""The bundle steps that protect data: a frame that is missing, changed,
half-written or outside the bundle is caught; a field nobody reads is not."""

from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "providers"))

from frame_sample import bundle
from frame_sample.bundle import BundleError


def _png(width: int = 4, height: int = 2) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _write(root: Path, mutate=None, identity=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    candidates = []
    for index, pts in enumerate((1001, 49049), start=1):
        name = f"cand-{index:04d}.png"
        body = _png()
        (root / name).write_bytes(body)
        candidates.append({
            "candidate_id": f"cand-{index:04d}", "artifact_name": name,
            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(),
            "intended_pts": pts, "observed_pts": pts, "time_base": "1/24000",
            "width": 4, "height": 2,
        })
    manifest = {
        "schema_version": "2.4", "bundle_id": "bundle-test", "created_at": "2026-01-02T03:04:05Z",
        "source_identity": identity or {
            "series_title": "A Series: Part 2?", "entry_type": "episode", "episode": 9,
            "episode_title": "An Episode", "source_filename": "a.mkv",
            "series_id": 1, "episode_ids": [2], "file_id": 3, "work_id": "a-series-e09",
        },
        "candidates": candidates,
    }
    if mutate:
        mutate(manifest)
    body = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (root / "manifest.json").write_bytes(body)
    (root / "READY.json").write_text(json.dumps({
        "state": "ready", "bundle_id": "bundle-test",
        "manifest_sha256": hashlib.sha256(body).hexdigest(),
    }))
    return root


def _digest(sha: str) -> str:
    return "d" * 64


def test_a_whole_bundle_passes(tmp_path):
    manifest, sha = bundle.check(_write(tmp_path / "b"))
    assert len(manifest["candidates"]) == 2 and len(sha) == 64


def test_fields_nobody_reads_are_not_an_error(tmp_path):
    def extra(manifest):
        manifest["a_fork_added_this"] = {"anything": 1}
        manifest["candidates"][0]["note"] = "also fine"
        manifest["source_identity"]["studio"] = "A Studio"
    bundle.check(_write(tmp_path / "b", mutate=extra))


def test_an_unfinished_bundle_is_refused(tmp_path):
    root = _write(tmp_path / "b")
    (root / "READY.json").unlink()
    with pytest.raises(BundleError, match="not finished"):
        bundle.check(root)


def test_a_list_changed_after_the_fact_is_refused(tmp_path):
    root = _write(tmp_path / "b")
    (root / "manifest.json").write_bytes((root / "manifest.json").read_bytes() + b" ")
    with pytest.raises(BundleError, match="changed after"):
        bundle.check(root)


def test_a_changed_frame_is_refused(tmp_path):
    root = _write(tmp_path / "b")
    body = bytearray((root / "cand-0001.png").read_bytes())
    body[-1] ^= 1
    (root / "cand-0001.png").write_bytes(bytes(body))
    with pytest.raises(BundleError, match="checksum"):
        bundle.check(root)


def test_a_short_frame_is_refused(tmp_path):
    root = _write(tmp_path / "b")
    (root / "cand-0002.png").write_bytes(_png()[:-5])
    with pytest.raises(BundleError, match="partly written"):
        bundle.check(root)


def test_a_missing_frame_is_refused(tmp_path):
    root = _write(tmp_path / "b")
    (root / "cand-0002.png").unlink()
    with pytest.raises(BundleError, match="missing"):
        bundle.check(root)


def test_a_frame_outside_the_bundle_is_refused(tmp_path):
    (tmp_path / "outside.png").write_bytes(_png())
    def escape(manifest):
        manifest["candidates"][0]["artifact_name"] = "../outside.png"
    with pytest.raises(BundleError, match="outside"):
        bundle.check(_write(tmp_path / "b", mutate=escape))


def test_a_repeated_frame_id_is_refused(tmp_path):
    def repeat(manifest):
        manifest["candidates"][1]["candidate_id"] = "cand-0001"
    with pytest.raises(BundleError, match="twice"):
        bundle.check(_write(tmp_path / "b", mutate=repeat))


@pytest.mark.parametrize("bad", ["../../escaped", "a/b", "..", "", "a\\b"])
def test_an_id_that_is_not_a_plain_name_is_refused(tmp_path, bad):
    def frame(manifest):
        manifest["candidates"][0]["candidate_id"] = bad
    with pytest.raises(BundleError, match="file name"):
        bundle.check(_write(tmp_path / "f", mutate=frame))
    def whole(manifest):
        manifest["bundle_id"] = bad
    with pytest.raises(BundleError, match="file name"):
        bundle.check(_write(tmp_path / "b", mutate=whole))


def test_a_frame_from_the_wrong_timestamp_is_refused(tmp_path):
    def drift(manifest):
        manifest["candidates"][0]["observed_pts"] += 1001
    with pytest.raises(BundleError, match="timestamp"):
        bundle.check(_write(tmp_path / "b", mutate=drift))


def test_a_wrong_declared_size_is_refused(tmp_path):
    def lie(manifest):
        manifest["candidates"][0]["width"] = 1920
    with pytest.raises(BundleError, match="image is"):
        bundle.check(_write(tmp_path / "b", mutate=lie))


def test_publish_names_the_folder_and_frames_for_a_person(tmp_path):
    prepared = bundle.publish(_write(tmp_path / "b"), tmp_path / "out", _digest)
    assert prepared.name == "20260102T030405Z--A-Series-Part-2--E009--An-Episode"
    assert sorted(p.name for p in (prepared / "candidates").iterdir()) == [
        "c0001--00h00m00s041.png", "c0002--00h00m02s043.png"]
    manifest, sha = bundle.check(prepared)
    assert manifest["candidates"][0]["artifact_name"] == "candidates/c0001--00h00m00s041.png"
    ready = json.loads((prepared / "READY.json").read_text())
    assert ready["manifest_sha256"] == sha and ready["bundle_digest"] == "d" * 64
    assert "Work ID: a-series-e09" in (prepared / "README.txt").read_text()


def test_publish_does_not_overwrite_an_earlier_folder(tmp_path):
    first = bundle.publish(_write(tmp_path / "b"), tmp_path / "out", _digest)
    second = bundle.publish(_write(tmp_path / "b"), tmp_path / "out", _digest)
    assert second != first and second.name.endswith("--f3")


@pytest.mark.parametrize("identity,label", [
    ({"entry_type": "movie"}, "MOVIE"),
    ({"entry_type": "episode", "season": 2, "episode": 7}, "S02E07"),
    ({"entry_type": "episode", "episode": 12}, "E012"),
])
def test_entry_labels(identity, label):
    assert bundle.entry_label(identity) == label


@pytest.mark.parametrize("text,slug", [
    ("Plain Title", "Plain-Title"),
    ('What: "A/B"?', "What-A-B"),
    ("trailing dots...", "trailing-dots"),
    ("CON", "CON-x"),
    ("x" * 80, "x" * 60),
])
def test_slugify(text, slug):
    assert bundle.slugify(text) == slug


def _fake_palette(monkeypatch, fail_on=None, abstain_on=None):
    def run(argv, **kwargs):
        if argv[1] == "--identity":
            return subprocess.CompletedProcess(argv, 0, "palette-descriptor-0.2.0\n", "")
        if fail_on and fail_on in argv[1]:
            return subprocess.CompletedProcess(argv, 1, "", "error: boom")
        if abstain_on and abstain_on in argv[1]:
            return subprocess.CompletedProcess(argv, 1, "", f"error: {bundle.PALETTE_ABSTENTION}")
        Path(argv[2]).write_text('{"descriptor_schema":"hikari-color-descriptor/1"}')
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(bundle.subprocess, "run", run)


def test_colors_are_described_per_frame_and_listed(tmp_path, monkeypatch):
    _fake_palette(monkeypatch, abstain_on="c0002")
    prepared = bundle.publish(_write(tmp_path / "b"), tmp_path / "out", _digest)
    final = bundle.describe_colors(prepared, tmp_path / "out" / "results",
                                   tool={"version": "t", "digest": "0" * 64}, preprocessing="p")
    assert final == tmp_path / "out" / "results" / "completed" / "bundle-test"
    listed = json.loads((final / "result-manifest.json").read_text())
    assert [c["status"] for c in listed["candidates"]] == ["success", "abstained"]
    for item in listed["candidates"]:
        body = (final / item["artifact_name"]).read_bytes()
        assert hashlib.sha256(body).hexdigest() == item["sha256"]
    abstained = json.loads((final / "artifacts" / "cand-0002.descriptor.json").read_text())
    assert abstained["status"] == "abstained"
    assert json.loads((final / "RESULT_READY.json").read_text())["state"] == "complete"
    assert [p.name for p in (final.parent).iterdir()] == ["bundle-test"], "no staging folder left"


def test_a_palette_failure_leaves_no_half_result(tmp_path, monkeypatch):
    _fake_palette(monkeypatch, fail_on="c0002")
    prepared = bundle.publish(_write(tmp_path / "b"), tmp_path / "out", _digest)
    with pytest.raises(BundleError, match="cand-0002"):
        bundle.describe_colors(prepared, tmp_path / "out" / "results",
                               tool={"version": "t", "digest": "0" * 64}, preprocessing="p")
    assert list((tmp_path / "out" / "results" / "completed").iterdir()) == []
