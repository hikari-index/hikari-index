"""Detect anime face boxes over a prepared candidate bundle.

This is face geometry, not people. research/02 is explicit that a face count is
not a person count: it misses profiles, rear views, masks, occlusion, distant
full bodies, and non-human characters, and it can count posters, screens,
reflections, and photographs. The output here is evidence for a fused bucket,
never a published count on its own.

Landmarks are computed but only their count and the box geometry are kept. No
identity, demographic, or trait inference is derived from a face box, and none
may be added later -- the protocol forbids it and the taxonomy has no field for
it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from .embed_bundle import (
    BundleValidationError,
    announce,
    device_report,
    peak_memory,
    resolve_device,
    sha256_bytes,
)
from .tag_bundle import _inputs

PIN_PATH = Path("/opt/hikari/models/anime-face-detector-PIN.json")
PROVIDER_VERSION = "0.1.0"

# The detector's own confidence cut. Faces below this are dropped before
# counting.
#
# The worry when this was set to 0.5 was that a low bar would let background
# texture inflate the lower bound the fusion depends on. Measured on 590
# frames across three works, that does not happen on this
# material: dropping to 0.1 recovers 30 frames and **every one carries a WD
# person tag**, so there were no clean false positives to find. This detector is
# precise and insensitive on anime, not the other way round.
#
# Do not read the gain as large. 0.5 -> 0.1 lifts composition coverage on one film
# from 33% to 38% and leaves 101 person-tagged frames still undetected, because
# the recall gap is profiles, rear views, extreme close-ups and stylised
# characters rather than confidence. It is a free five points, not a fix.
DEFAULT_FACE_SCORE = 0.1


def run_detection(
    bundle_dir: Path,
    out_dir: Path,
    device: str = "auto",
    face_score: float = DEFAULT_FACE_SCORE,
    detector: str = "faster-rcnn",
    bundle_id: Optional[str] = None,
    loose: Optional[tuple[Path, Path]] = None,
) -> dict:
    """loose = (frames dir, candidates file), as tag_bundle.run_tagging."""
    from anime_face_detector import create_detector
    from PIL import Image
    import numpy as np

    items, source = _inputs(bundle_dir, bundle_id, loose)
    resolved_device = resolve_device(device)
    announce("face", resolved_device)
    started = time.perf_counter()
    torch_device = "cuda:0" if resolved_device == "cuda" else "cpu"
    model = create_detector(face_detector_name=detector, device=torch_device)

    results = []
    for candidate in items:
        with Image.open(candidate["path"]) as raw:
            image = np.asarray(raw.convert("RGB"))
        # The package expects BGR, matching its OpenCV lineage.
        predictions = model(image[:, :, ::-1])

        width, height = image.shape[1], image.shape[0]
        faces = []
        for prediction in predictions:
            box = prediction["bbox"]
            score = float(box[4]) if len(box) > 4 else 1.0
            if score < face_score:
                continue
            x0, y0, x1, y1 = (float(v) for v in box[:4])
            area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
            faces.append({
                "score": round(score, 6),
                "box": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                # Fraction of the frame the face occupies: the input to shot
                # scale later, and a way to tell a foreground face from a
                # distant one without storing pixels.
                "frame_fraction": round(area / (width * height), 8),
                "landmarks": len(prediction.get("keypoints", [])),
            })

        faces.sort(key=lambda f: -f["frame_fraction"])
        results.append({
            "candidate_id": candidate["candidate_id"],
            "shot_id": candidate["shot_id"],
            "face_count": len(faces),
            "largest_face_fraction": faces[0]["frame_fraction"] if faces else 0.0,
            "faces": faces,
        })
        print(f"[face] {candidate['candidate_id']}: {len(faces)} faces "
              f"at >= {face_score}", flush=True)

    pin = json.loads(PIN_PATH.read_text(encoding="utf-8")) if PIN_PATH.is_file() else {}
    result_manifest = {
        "schema_version": "1.0",
        "provider": "anime-face-detector",
        "provider_version": PROVIDER_VERSION,
        "model_pin": pin,
        "detector": detector,
        "runtime": {
            **device_report(resolved_device),
            **peak_memory(resolved_device),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "candidates": len(results),
        },
        "device": resolved_device,
        "face_score_threshold": face_score,
        **source,
        "counting_caveat": (
            "face count is a lower bound on people, not a people count; it misses "
            "profiles, rear views, occlusion and non-human characters, and can "
            "count posters, screens and reflections"
        ),
        "identity_inference": "none; boxes and geometry only",
        "candidates": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(result_manifest, sort_keys=True, indent=2) + "\n").encode()
    (out_dir / "result-manifest.json").write_bytes(manifest_bytes)
    (out_dir / "RESULT_READY.json").write_bytes(
        (json.dumps({
            "schema_version": "1.0",
            "state": "ready",
            "provider": "anime-face-detector",
            "result_manifest_sha256": sha256_bytes(manifest_bytes),
        }, sort_keys=True, indent=2) + "\n").encode()
    )
    return result_manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="detect-faces",
        description="Detect anime face boxes over a prepared candidate bundle",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle")
    source.add_argument("--loose", help="directory of <candidate_id>.png loose frames "
                                        "(needs --candidates)")
    parser.add_argument("--candidates", help="with --loose: JSON naming the frames")
    parser.add_argument("--out", required=True)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--face-score", type=float, default=DEFAULT_FACE_SCORE)
    parser.add_argument("--detector", choices=("faster-rcnn", "yolov3"),
                        default="faster-rcnn")
    parser.add_argument("--bundle-id", default=None)
    args = parser.parse_args(argv)
    if args.loose and not args.candidates:
        parser.error("--loose needs --candidates")

    run_detection(
        Path(args.bundle) if args.bundle else None, Path(args.out), device=args.device,
        face_score=args.face_score, detector=args.detector,
        bundle_id=args.bundle_id,
        loose=(Path(args.loose), Path(args.candidates)) if args.loose else None,
    )
    print("FACE_DETECTION_COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BundleValidationError, OSError, ValueError) as error:
        print(f"detect-faces: {error}", file=sys.stderr)
        sys.exit(1)
