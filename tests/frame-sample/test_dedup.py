"""Tests for perceptual hash dedup."""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers" / "frame_sample"
sys.path.insert(0, str(PROVIDER_ROOT.parent))

from frame_sample.dedup import compute_phash_bytes, group_dedup_families


def _make_gradient_png(direction: str = "horizontal", width: int = 64, height: int = 64) -> bytes:
    from PIL import Image
    img = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            if direction == "horizontal":
                v = int(255 * x / width)
                img.putpixel((x, y), (v, 0, 0))
            elif direction == "vertical":
                v = int(255 * y / height)
                img.putpixel((x, y), (0, v, 0))
            elif direction == "diagonal":
                v = int(255 * (x + y) / (width + height))
                img.putpixel((x, y), (0, 0, v))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_noise_png(seed: int = 0, width: int = 64, height: int = 64) -> bytes:
    import random
    from PIL import Image
    rng = random.Random(seed)
    img = Image.new("RGB", (width, height))
    for y in range(height):
        for x in range(width):
            img.putpixel((x, y), (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255)))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestPHashDedup:
    def test_identical_frames_same_hash(self):
        png1 = _make_gradient_png("horizontal")
        png2 = _make_gradient_png("horizontal")
        h1 = compute_phash_bytes(png1)
        h2 = compute_phash_bytes(png2)
        assert h1 == h2

    def test_different_frames_different_hash(self):
        png_h = _make_gradient_png("horizontal")
        png_v = _make_gradient_png("vertical")
        h_h = compute_phash_bytes(png_h)
        h_v = compute_phash_bytes(png_v)
        assert h_h != h_v

    def test_duplicate_family_grouping(self):
        png_n1 = _make_noise_png(seed=100)
        png_n2 = _make_noise_png(seed=200)
        png_n3 = _make_noise_png(seed=300)
        h_n1 = compute_phash_bytes(png_n1)
        h_n2 = compute_phash_bytes(png_n2)
        h_n3 = compute_phash_bytes(png_n3)
        candidates = [
            ("cand-0001", h_n1),
            ("cand-0002", h_n1),
            ("cand-0003", h_n2),
            ("cand-0004", h_n1),
            ("cand-0005", h_n2),
        ]
        families = group_dedup_families(candidates, threshold=5)
        assert len(families) == 2, f"expected 2 families, got {len(families)}"
        n1_family = next(f for f in families if "cand-0001" in f.candidate_ids)
        n2_family = next(f for f in families if "cand-0003" in f.candidate_ids)
        assert set(n1_family.candidate_ids) == {"cand-0001", "cand-0002", "cand-0004"}
        assert set(n2_family.candidate_ids) == {"cand-0003", "cand-0005"}

    def test_no_duplicates_no_families(self):
        png_n1 = _make_noise_png(seed=1)
        png_n2 = _make_noise_png(seed=2)
        png_n3 = _make_noise_png(seed=3)
        candidates = [
            ("cand-0001", compute_phash_bytes(png_n1)),
            ("cand-0002", compute_phash_bytes(png_n2)),
            ("cand-0003", compute_phash_bytes(png_n3)),
        ]
        families = group_dedup_families(candidates, threshold=5)
        assert len(families) == 0

    def test_near_identical_within_threshold(self):
        from PIL import Image
        img1 = Image.new("RGB", (64, 64))
        for y in range(64):
            for x in range(64):
                v = int(255 * x / 64)
                img1.putpixel((x, y), (v, 0, 0))
        img2 = img1.copy()
        img2.putpixel((0, 0), (1, 0, 0))
        buf1 = io.BytesIO()
        buf2 = io.BytesIO()
        img1.save(buf1, format="PNG")
        img2.save(buf2, format="PNG")
        h1 = compute_phash_bytes(buf1.getvalue())
        h2 = compute_phash_bytes(buf2.getvalue())
        candidates = [("c1", h1), ("c2", h2)]
        families = group_dedup_families(candidates, threshold=5)
        assert len(families) == 1
