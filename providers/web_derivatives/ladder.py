"""AVIF derivative ladder for published candidate bundles.

DERIVATIVE_POLICY freezes the encode accepted in ADR-0006: AVIF quality 85,
speed 4, 4:4:4 chroma, encoded from the published master's samples via
Pillow/libavif, which writes the full CICP sRGB declaration. Tier widths
follow the delivery ladder (320 / 640 / 960 / 1920-or-source, whichever is
lower). The source is never upscaled; a tier wider than the source collapses
into the source width, and the native-width tier re-encodes the master's own
pixels with no resampling — that is the exact encode the quality judgment
was made on. Smaller tiers are downscaled with Lanczos before the same
encode; per the top-down rule they may later adopt more aggressive settings,
which would be a new policy version.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import PIL
from PIL import Image, features

DERIVATIVE_POLICY = "avif-q85-s4-444-lanczos-v1"
TIER_WIDTHS = (320, 640, 960, 1920)
ENCODE_KWARGS = {"quality": 85, "speed": 4, "subsampling": "4:4:4"}

# The CICP declaration Pillow/libavif writes for an sRGB save, verified by
# ffprobe when the encode was accepted. An emitted file declaring anything
# else is a defect (ADR-0006: an untagged or mis-tagged derivative is a
# defect whatever its pixels look like).
EXPECTED_CICP = {
    "color_range": "pc",
    "color_space": "smpte170m",
    "color_transfer": "iec61966-2-1",
    "color_primaries": "bt709",
}


class DerivativeError(RuntimeError):
    pass


def ladder_widths(source_width: int) -> list[int]:
    """Tier widths for a source, never upscaling, duplicates collapsed."""
    if source_width < 1:
        raise DerivativeError(f"invalid source width {source_width}")
    return sorted({min(w, source_width) for w in TIER_WIDTHS})


def tier_size(source_width: int, source_height: int, width: int) -> tuple[int, int]:
    if width == source_width:
        return source_width, source_height
    return width, max(1, round(source_height * width / source_width))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class TierResult:
    width: int
    height: int
    bytes: int
    sha256: str
    file: str


def encode_ladder(master: Path, dest_dir: Path) -> list[TierResult]:
    """Encode every tier for one master PNG into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    image = Image.open(master).convert("RGB")
    results = []
    for width in ladder_widths(image.width):
        w, h = tier_size(image.width, image.height, width)
        tier = image if w == image.width else image.resize((w, h), Image.LANCZOS)
        dest = dest_dir / f"w{w}.avif"
        tier.save(dest, "AVIF", **ENCODE_KWARGS)
        results.append(
            TierResult(w, h, dest.stat().st_size, _sha256(dest), dest.name)
        )
    return results


def build_for_bundle(
    bundle_dir: Path,
    out_dir: Path,
    candidate_ids: Optional[Iterable[str]] = None,
    surplus_dir: Optional[Path] = None,
) -> dict:
    """Encode the ladder for every published candidate in a prepared bundle.

    Candidates are mapped through manifest.json `artifact_name` — never
    through filename order. Returns the derivative manifest (also written to
    out_dir / derivatives-manifest.json).

    `surplus_dir` lets a wanted candidate that the manifest does not list be
    read from the pipeline's surplus folder (`<candidate_id>.png`, kept
    beside the bundle since image `.10`). The stratified picker (ADR-0008)
    chooses from the whole measured pool, so its selection can name surplus
    frames; until the bundle carries the pool itself, this is how those
    masters reach the ladder. Surplus is not checksum-bound, so the entry
    records the digest this encoder computed and is marked `"source":
    "surplus"`.
    """
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.is_file():
        raise DerivativeError(f"no manifest.json under {bundle_dir}")
    bundle = json.loads(manifest_path.read_text(encoding="utf-8"))
    wanted = set(candidate_ids) if candidate_ids is not None else None

    entries = []
    for candidate in bundle["candidates"]:
        cid = candidate["candidate_id"]
        if wanted is not None and cid not in wanted:
            continue
        master = bundle_dir / candidate["artifact_name"]
        if not master.is_file():
            raise DerivativeError(f"{cid}: missing artifact {master}")
        tiers = encode_ladder(master, out_dir / cid)
        entries.append(
            {
                "candidate_id": cid,
                "source_artifact": candidate["artifact_name"],
                "source_sha256": candidate["sha256"],
                "source_width": candidate["width"],
                "source_height": candidate["height"],
                "tiers": [vars(t) for t in tiers],
            }
        )

    if wanted is not None:
        missing = wanted - {e["candidate_id"] for e in entries}
        if missing and surplus_dir is not None:
            for cid in sorted(missing):
                master = surplus_dir / f"{cid}.png"
                if not master.is_file():
                    continue
                with Image.open(master) as probe:
                    width, height = probe.size
                tiers = encode_ladder(master, out_dir / cid)
                entries.append(
                    {
                        "candidate_id": cid,
                        "source": "surplus",
                        "source_artifact": f"surplus/{master.name}",
                        "source_sha256": _sha256(master),
                        "source_width": width,
                        "source_height": height,
                        "tiers": [vars(t) for t in tiers],
                    }
                )
            missing = wanted - {e["candidate_id"] for e in entries}
        if missing:
            where = "bundle or surplus" if surplus_dir is not None else "bundle"
            raise DerivativeError(f"candidates not in {where}: {sorted(missing)}")

    manifest = {
        "derivative_policy": DERIVATIVE_POLICY,
        "encoder": {
            "pillow": PIL.__version__,
            "libavif": features.version("avif"),
        },
        "bundle_id": bundle.get("bundle_id"),
        "candidates": entries,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "derivatives-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
