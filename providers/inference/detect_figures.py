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
every analyze image: measured in the CPU image on 4 CPUs, about 60 ms a frame
per model (plus reading the frame, which every provider pays), less than the
tagger or the face detector take, so a GPU path is not worth a second runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from .embed_bundle import BundleValidationError, sha256_bytes
from .tag_bundle import _inputs

MODELS = Path("/opt/hikari/models/deepghs")
PIN_PATH = MODELS / "PIN.json"
# 0.2.0: imgutils' default preprocessing (640x640 stretch, bicubic), its
# NMS and box rounding; 0.1.0 kept the aspect and used bilinear.
PROVIDER_VERSION = "0.2.0"

# The model cards' F1-optimal score cuts. Boxes below them are dropped.
HEAD_SCORE = 0.413
PERSON_SCORE = 0.324
# Overlap above which two boxes are one detection (imgutils' default for
# these models is 0.7 for heads, 0.5 for figures).
HEAD_IOU = 0.7
PERSON_IOU = 0.5
# The square the detector sees. imgutils' default (yolo_predict without
# allow_dynamic) stretches every frame to it; the head-height cuts in
# annotation/shot_scale.py were calibrated on exactly this input.
INPUT_SIDE = 640


def _cgroup_quota() -> Optional[int]:
    """A container's CPU limit in whole CPUs, or None: cgroup v2 cpu.max,
    else cgroup v1 cpu.cfs_quota_us / cpu.cfs_period_us."""
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text().split()[:2]
        return None if quota == "max" else max(1, int(int(quota) / int(period)))
    except (OSError, ValueError):
        pass
    for folder in ("/sys/fs/cgroup/cpu", "/sys/fs/cgroup/cpu,cpuacct"):
        try:
            quota = int(Path(folder, "cpu.cfs_quota_us").read_text())
            period = int(Path(folder, "cpu.cfs_period_us").read_text())
        except (OSError, ValueError):
            continue
        return None if quota <= 0 or period <= 0 else max(1, int(quota / period))
    return None


def _threads() -> int:
    """CPUs this process may use: the CPUs it may run on, capped by a
    container's CPU limit. onnxruntime otherwise starts one thread per host
    core; measured 2026-10-06 under a 4-CPU limit on a 24-core host, that
    ran 205 ms a frame against 57 ms with 4 threads."""
    import os
    try:
        allowed = len(os.sched_getaffinity(0))
    except AttributeError:
        allowed = os.cpu_count() or 1
    quota = _cgroup_quota()
    return max(1, min(allowed, quota) if quota else allowed)


def _session(path: Path):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = _threads()
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def _nms(boxes, scores, iou):
    """imgutils' NMS, +1 pixel arithmetic included, so the same boxes
    survive as in the reference."""
    import numpy as np
    order = scores.argsort()[::-1]
    keep = []
    area = (boxes[:, 2] - boxes[:, 0] + 1) * (boxes[:, 3] - boxes[:, 1] + 1)
    while order.size:
        i = order[0]
        keep.append(int(i))
        rest = order[1:]
        x0 = np.maximum(boxes[i, 0], boxes[rest, 0])
        y0 = np.maximum(boxes[i, 1], boxes[rest, 1])
        x1 = np.minimum(boxes[i, 2], boxes[rest, 2])
        y1 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, x1 - x0 + 1) * np.maximum(0.0, y1 - y0 + 1)
        overlap = inter / (area[i] + area[rest] - inter)
        order = rest[overlap <= iou]
    return keep


def prepare(image):
    """The model input imgutils makes by default: the frame stretched to
    640x640 with Pillow's bicubic resize, scaled to 0..1, NCHW. Both models
    take the same input, so it is made once per frame."""
    import numpy as np
    from PIL import Image
    x = np.asarray(image.resize((INPUT_SIDE, INPUT_SIDE), Image.Resampling.BICUBIC),
                   dtype=np.float32) / 255.0
    return x.transpose(2, 0, 1)[None]


def detect(session, image, score_cut: float, iou: float, prepared=None) -> list[dict]:
    """One YOLOv8 pass as imgutils runs these models by default: the input
    from prepare(), scores above the cut, NMS, boxes mapped back to the
    frame's pixels, rounded and clipped to it."""
    import numpy as np
    width, height = image.size
    x = prepare(image) if prepared is None else prepared
    out = session.run(None, {session.get_inputs()[0].name: x})[0][0]
    if out.shape[0] != 5:  # one class: rows are cx, cy, w, h, score
        out = out.T
    keep = out[4] > score_cut
    cx, cy, bw, bh = out[:4, keep]
    scores = out[4, keep]
    if not scores.size:
        return []
    boxes = np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1)
    def px(value, limit):
        # imgutils' _xy_postprocess, same order and dtype: the float32 value
        # divided by the input side, times the frame side, clipped, rounded
        # half to even. Doing it in float64 moves an edge at .5 by a pixel.
        return int(np.clip(value / INPUT_SIDE * limit, a_min=0, a_max=limit).round())

    found = []
    for i in _nms(boxes, scores, iou):
        x0, y0 = px(boxes[i, 0], width), px(boxes[i, 1], height)
        x1, y1 = px(boxes[i, 2], width), px(boxes[i, 3], height)
        found.append({
            "score": round(float(scores[i]), 6),
            "box": [x0, y0, x1, y1],
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
        x = prepare(image)
        heads = detect(heads_model, image, HEAD_SCORE, HEAD_IOU, x)
        persons = detect(persons_model, image, PERSON_SCORE, PERSON_IOU, x)
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
            "threads": _threads(),
            "elapsed_seconds": round(elapsed, 3),
            "candidates": len(results),
            "ms_per_frame": round(1000 * elapsed / max(1, len(results)), 1),
        },
        "score_cuts": {"head": HEAD_SCORE, "person": PERSON_SCORE},
        "settings": {
            "input": f"{INPUT_SIDE}x{INPUT_SIDE} stretch, Pillow bicubic, 0..1",
            "nms_iou": {"head": HEAD_IOU, "person": PERSON_IOU},
            "boxes": "imgutils _xy_postprocess (float32, clipped, round half to even)",
        },
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
