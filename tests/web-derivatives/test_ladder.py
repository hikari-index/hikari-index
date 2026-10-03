"""Derivative ladder: tier arithmetic, bundle mapping, manifest binding."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from PIL import Image, features

from web_derivatives.ladder import (
    DERIVATIVE_POLICY,
    DerivativeError,
    build_for_bundle,
    ladder_widths,
    tier_size,
)

pytestmark = pytest.mark.skipif(
    not features.check("avif"), reason="Pillow lacks AVIF support"
)


def test_ladder_widths_full_ladder():
    assert ladder_widths(1920) == [320, 640, 960, 1920]
    assert ladder_widths(3840) == [320, 640, 960, 1920]


def test_ladder_widths_collapse_never_upscales():
    assert ladder_widths(1280) == [320, 640, 960, 1280]
    assert ladder_widths(720) == [320, 640, 720]
    assert ladder_widths(500) == [320, 500]
    assert ladder_widths(200) == [200]


def test_tier_size_native_is_exact_and_scaled_keeps_aspect():
    assert tier_size(1920, 816, 1920) == (1920, 816)
    assert tier_size(1920, 816, 640) == (640, 272)
    assert tier_size(1920, 1080, 320) == (320, 180)


def synthetic_bundle(root: Path, dims: dict[str, tuple[int, int]]) -> Path:
    bundle = root / "prepared-bundle"
    (bundle / "candidates").mkdir(parents=True)
    candidates = []
    for index, (cid, (w, h)) in enumerate(dims.items()):
        name = f"candidates/c{index:04d}.png"
        image = Image.new("RGB", (w, h))
        for x in range(w):
            for y in range(0, h, max(1, h // 8)):
                image.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
        image.save(bundle / name)
        candidates.append(
            {
                "candidate_id": cid,
                "artifact_name": name,
                "sha256": hashlib.sha256(
                    (bundle / name).read_bytes()
                ).hexdigest(),
                "width": w,
                "height": h,
            }
        )
    (bundle / "manifest.json").write_text(
        json.dumps({"bundle_id": "synthetic", "candidates": candidates}),
        encoding="utf-8",
    )
    return bundle


def test_build_for_bundle_emits_ladder_and_manifest(tmp_path):
    bundle = synthetic_bundle(
        tmp_path, {"cand-0001": (640, 360), "cand-0002": (320, 180)}
    )
    out = tmp_path / "out"
    manifest = build_for_bundle(bundle, out)

    assert manifest["derivative_policy"] == DERIVATIVE_POLICY
    by_id = {e["candidate_id"]: e for e in manifest["candidates"]}
    assert [t["width"] for t in by_id["cand-0001"]["tiers"]] == [320, 640]
    assert [t["width"] for t in by_id["cand-0002"]["tiers"]] == [320]

    for entry in manifest["candidates"]:
        for tier in entry["tiers"]:
            path = out / entry["candidate_id"] / tier["file"]
            assert path.is_file()
            assert (
                hashlib.sha256(path.read_bytes()).hexdigest() == tier["sha256"]
            )
            with Image.open(path) as decoded:
                assert decoded.size == (tier["width"], tier["height"])

    written = json.loads(
        (out / "derivatives-manifest.json").read_text(encoding="utf-8")
    )
    assert written == manifest


def test_build_for_bundle_candidate_filter_and_unknown_id(tmp_path):
    bundle = synthetic_bundle(tmp_path, {"cand-0001": (400, 300)})
    out = tmp_path / "out"
    manifest = build_for_bundle(bundle, out, ["cand-0001"])
    assert len(manifest["candidates"]) == 1
    with pytest.raises(DerivativeError):
        build_for_bundle(bundle, tmp_path / "out2", ["cand-9999"])


def test_build_for_bundle_reads_wanted_surplus_master(tmp_path):
    """A wanted id the manifest lacks is read from the surplus folder,
    encoded like any master, and recorded with a computed digest and a
    `source: surplus` marker; an id found nowhere is still an error."""
    bundle = synthetic_bundle(tmp_path, {"cand-0001": (400, 300)})
    surplus = tmp_path / "surplus"
    surplus.mkdir()
    Image.new("RGB", (640, 360), (20, 120, 200)).save(surplus / "cand-0777.png")

    out = tmp_path / "out"
    manifest = build_for_bundle(
        bundle, out, ["cand-0001", "cand-0777"], surplus_dir=surplus
    )
    by_id = {e["candidate_id"]: e for e in manifest["candidates"]}
    assert set(by_id) == {"cand-0001", "cand-0777"}
    assert "source" not in by_id["cand-0001"]
    entry = by_id["cand-0777"]
    assert entry["source"] == "surplus"
    assert entry["source_artifact"] == "surplus/cand-0777.png"
    assert entry["source_sha256"] == hashlib.sha256(
        (surplus / "cand-0777.png").read_bytes()
    ).hexdigest()
    assert (entry["source_width"], entry["source_height"]) == (640, 360)
    assert [t["width"] for t in entry["tiers"]] == [320, 640]
    assert (out / "cand-0777" / "w640.avif").is_file()

    with pytest.raises(DerivativeError, match="bundle or surplus"):
        build_for_bundle(
            bundle, tmp_path / "out2", ["cand-8888"], surplus_dir=surplus
        )
