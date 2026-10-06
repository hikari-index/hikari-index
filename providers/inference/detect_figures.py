"""Detect anime heads and whole figures over a prepared candidate bundle.

The face detector sees faces: it misses the back of a head, a profile, a
figure far off, so shot scale and composition abstained on about a third of
frames (#16). These two small detectors, trained on anime by deepghs, see
the head and the whole figure. They are a second source beside the face
lane, never a replacement: annotation fusion uses a head only where no
face answered.

Boxes and geometry only. No identity, demographic or trait output, and none
may be added: the protocol forbids it and the taxonomy has no field for it.

Models (MIT on their model cards; trained with Ultralytics, whose AGPL-3.0
the weights' own metadata names, which this AGPL-3.0 project can carry):
deepghs/anime_head_detection head_detect_v2.0_s and
deepghs/anime_person_detection person_detect_v1.3_s, ONNX, pinned by commit
and checked by sha256 in the image build. Run with onnxruntime on CPU on
every analyze image: about 50 ms a frame each, so a GPU path is not worth a
second runtime.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Optional

from .embed_bundle import BundleValidationError, sha256_bytes
from .tag_bundle import _inputs

MODELS = Path("/opt/hikari/models/deepghs")
PIN_PATH = MODELS / "PIN.json"
PROVIDER_VERSION = "0.1.0"

# The model cards' F1-optimal score cuts. Boxes below them are dropped.
HEAD_SCORE = 0.413
PERSON_SCORE = 0.324
# Overlap above which two boxes are one detection (imgutils' default for
# these models is 0.7 for heads, 0.5 for figures).
HEAD_IOU = 0.7
PERSON_IOU = 0.5
# Longest side of the image the detector sees, as imgutils runs them.
MAX_SIDE = 640
ALIGN = 32


def _session(path: Path):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def _nms(boxes, scores, iou):
    import numpy as np
    order = scores.argsort()[::-1]
    keep = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        x0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(x1 - x0, 0, None) * np.clip(y1 - y0, 0, None)
        overlap = inter / (area[i] + area[rest] - inter + 1e-9)
        order = rest[overlap <= iou]
    return keep


def detect(session, image, score_cut: float, iou: float) -> list[dict]:
    """One YOLOv8 pass the way imgutils runs these models: keep the aspect,
    longest side to 640, both sides rounded up to a multiple of 32, no
    padding; boxes mapped back to the frame's own pixels."""
    import numpy as np
    from PIL import Image
    width, height = image.size
    ratio = min(MAX_SIDE / width, MAX_SIDE / height, 1.0)
    w = int(math.ceil(width * ratio / ALIGN) * ALIGN)
    h = int(math.ceil(height * ratio / ALIGN) * ALIGN)
    x = np.asarray(image.resize((w, h), Image.BILINEAR), dtype=np.float32) / 255.0
    out = session.run(None, {session.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})[0][0]
    if out.shape[0] != 5:  # one class: rows are cx, cy, w, h, score
        out = out.T
    keep = out[4] >= score_cut
    cx, cy, bw, bh = out[:4, keep]
    scores = out[4, keep]
    if not scores.size:
        return []
    boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
    sx, sy = width / w, height / h
    found = []
    for i in _nms(boxes, scores, iou):
        x0, y0 = max(0.0, boxes[i, 0] * sx), max(0.0, boxes[i, 1] * sy)
        x1, y1 = min(float(width), boxes[i, 2] * sx), min(float(height), boxes[i, 3] * sy)
        found.append({
            "score": round(float(scores[i]), 6),
            "box": [round(float(x0), 2), round(float(y0), 2), round(float(x1), 2), round(float(y1), 2)],
            # Share of the frame's height: the shot-scale cue, independent
            # of the frame's aspect.
            "height_fraction": round(float((y1 - y0) / height), 6),
        })
    found.sort(key=lambda b: -(b["box"][2] - b["box"][0]) * (b["box"][3] - b["box"][1]))
    return found


def run_detection(bundle_dir: Optional[Path], out_dir: Path,
                  bundle_id: Optional[str] = None,
                  loose: Optional[tuple[Path, Path]] = None,
                  models: Path = MODELS) -> dict:
    """loose = (frames dir, candidates file), as tag_bundle.run_tagging."""
    from PIL import Image

    items, source = _inputs(bundle_dir, bundle_id, loose)
    started = time.perf_counter()
    heads_model = _session(models / "head_detect_v2.0_s" / "model.onnx")
    persons_model = _session(models / "person_detect_v1.3_s" / "model.onnx")
    results = []
    for candidate in items:
        with Image.open(candidate["path"]) as raw:
            image = raw.convert("RGB")
        heads = detect(heads_model, image, HEAD_SCORE, HEAD_IOU)
        persons = detect(persons_model, image, PERSON_SCORE, PERSON_IOU)
        results.append({
            "candidate_id": candidate["candidate_id"],
            "shot_id": candidate["shot_id"],
            "width": image.width,
            "height": image.height,
            "head_count": len(heads),
            "person_count": len(persons),
            "heads": heads,
            "persons": persons,
        })
        print(f"[figures] {candidate['candidate_id']}: {len(heads)} heads, "
              f"{len(persons)} figures", flush=True)

    pin_file = models / "PIN.json"
    pin = json.loads(pin_file.read_text(encoding="utf-8")) if pin_file.is_file() else {}
    elapsed = time.perf_counter() - started
    result_manifest = {
        "schema_version": "1.0",
        "provider": "anime-figure-detector",
        "provider_version": PROVIDER_VERSION,
        "model_pin": pin,
        "runtime": {
            "device": "cpu",
            "elapsed_seconds": round(elapsed, 3),
            "candidates": len(results),
            "ms_per_frame": round(1000 * elapsed / max(1, len(results)), 1),
        },
        "score_cuts": {"head": HEAD_SCORE, "person": PERSON_SCORE},
        **source,
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
            "provider": "anime-figure-detector",
            "result_manifest_sha256": sha256_bytes(manifest_bytes),
        }, sort_keys=True, indent=2) + "\n").encode()
    )
    return result_manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="detect-figures",
        description="Detect anime heads and whole figures over a prepared candidate bundle",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle")
    source.add_argument("--loose", help="directory of <candidate_id>.png loose frames "
                                        "(needs --candidates)")
    parser.add_argument("--candidates", help="with --loose: JSON naming the frames")
    parser.add_argument("--out", required=True)
    parser.add_argument("--bundle-id", default=None)
    args = parser.parse_args(argv)
    if args.loose and not args.candidates:
        parser.error("--loose needs --candidates")
    run_detection(
        Path(args.bundle) if args.bundle else None, Path(args.out),
        bundle_id=args.bundle_id,
        loose=(Path(args.loose), Path(args.candidates)) if args.loose else None,
    )
    print("FIGURE_DETECTION_COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BundleValidationError, OSError, ValueError) as error:
        print(f"detect-figures: {error}", file=sys.stderr)
        sys.exit(1)
