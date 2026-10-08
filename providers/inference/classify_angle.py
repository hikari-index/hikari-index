"""Classify each candidate's camera angle: low, eye level, high, overhead or
Dutch.

The tagger names an angle on almost no frames (2 of 66, then 2 of 148, on
two blind grades). This classifier answers on every frame. On 148 frames
from eleven shows no rule was ever fitted on, graded blind by the
maintainer (2026-10-07), it agreed with the human on 80% against 53% for
always answering eye level; the human and the model split mostly on low
against eye level, where a second, blind reader sided with each about half
the time.

Model (Apache-2.0 on its model card): aslakey/camera_angle, a fine-tune of
facebook/dinov2-with-registers-large (Apache-2.0) on live-action stills,
pinned by commit and checked by sha256 in the image build. About 300M
parameters, 1.2 GB.

The whole frame, squeezed to 224x224, is what it sees. The model card's
processor resizes the short side to 256 and crops the middle 224, which
cuts the sides off a wide frame; on the second grade that scored 71%
against 80%, and on the first the two tied. Pillow's bicubic resize,
then ImageNet mean and std, exactly as transformers' BitImageProcessor
(the card's processor) does it, done here so a transformers upgrade cannot
change the input.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from .detect_figures import _threads
from .embed_bundle import BundleValidationError, resolve_device, sha256_bytes
from .tag_bundle import _inputs

MODEL_DIR = Path("/opt/hikari/models/aslakey-camera-angle")
PROVIDER_VERSION = "0.1.0"
INPUT_SIDE = 224
MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
# The model's own names, and the taxonomy's for them.
TAXONOMY_NAME = {"low": "low", "neutral": "eye-level", "high": "high",
                 "overhead": "overhead", "dutch": "dutch"}


def prepare(image):
    """The whole frame at 224x224, Pillow bicubic, scaled 0..1, normalized
    with ImageNet's mean and std, NCHW float32."""
    import numpy as np
    from PIL import Image
    x = np.asarray(image.resize((INPUT_SIDE, INPUT_SIDE), Image.Resampling.BICUBIC),
                   dtype=np.float32) / 255.0
    x = (x - np.asarray(MEAN, dtype=np.float32)) / np.asarray(STD, dtype=np.float32)
    return x.transpose(2, 0, 1)[None].astype(np.float32)


def classify(model, image, device: str = "cpu") -> dict:
    """The label with the highest logit (the card's rule; the model was
    trained multi-label) and every label's sigmoid score."""
    import torch
    with torch.no_grad():
        logits = model(pixel_values=torch.from_numpy(prepare(image)).to(device)).logits[0].float().cpu()
    names = [model.config.id2label[i] for i in range(len(logits))]
    scores = torch.sigmoid(logits).tolist()
    top = int(logits.argmax())
    return {
        "angle": TAXONOMY_NAME[names[top]],
        "score": round(float(scores[top]), 6),
        "scores": {TAXONOMY_NAME[n]: round(float(s), 6) for n, s in zip(names, scores)},
    }


def run_classification(bundle_dir: Optional[Path], out_dir: Path,
                       bundle_id: Optional[str] = None,
                       loose: Optional[tuple[Path, Path]] = None,
                       model_dir: Path = MODEL_DIR, device: str = "auto") -> dict:
    """loose = (frames dir, candidates file), as tag_bundle.run_tagging."""
    import torch
    from PIL import Image
    from transformers import AutoModelForImageClassification

    items, source = _inputs(bundle_dir, bundle_id, loose)
    device = resolve_device(device)
    if device == "cpu":
        torch.set_num_threads(_threads())
    started = time.perf_counter()
    model = AutoModelForImageClassification.from_pretrained(str(model_dir)).to(device).eval()
    results = []
    for candidate in items:
        with Image.open(candidate["path"]) as raw:
            image = raw.convert("RGB")
        answer = classify(model, image, device)
        results.append({"candidate_id": candidate["candidate_id"],
                        "shot_id": candidate["shot_id"], **answer})
        print(f"[angle] {candidate['candidate_id']}: {answer['angle']} {answer['score']:.2f}", flush=True)

    pin_file = model_dir / "PIN.json"
    pin = json.loads(pin_file.read_text(encoding="utf-8")) if pin_file.is_file() else {}
    elapsed = time.perf_counter() - started
    result_manifest = {
        "schema_version": "1.0",
        "provider": "camera-angle-classifier",
        "provider_version": PROVIDER_VERSION,
        "model_pin": pin,
        "runtime": {
            "device": device,
            "threads": torch.get_num_threads() if device == "cpu" else None,
            "elapsed_seconds": round(elapsed, 3),
            "candidates": len(results),
            "ms_per_frame": round(1000 * elapsed / max(1, len(results)), 1),
        },
        "settings": {
            "input": f"whole frame, {INPUT_SIDE}x{INPUT_SIDE} stretch, Pillow bicubic, ImageNet mean/std",
            "label": "highest logit; scores are each label's sigmoid",
        },
        **source,
        "identity_inference": "none; camera angle only",
        "candidates": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(result_manifest, sort_keys=True, indent=2) + "\n").encode()
    (out_dir / "result-manifest.json").write_bytes(manifest_bytes)
    (out_dir / "RESULT_READY.json").write_bytes(
        (json.dumps({
            "schema_version": "1.0",
            "state": "ready",
            "provider": "camera-angle-classifier",
            "result_manifest_sha256": sha256_bytes(manifest_bytes),
        }, sort_keys=True, indent=2) + "\n").encode()
    )
    return result_manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="classify-angle",
        description="Classify each candidate's camera angle over a prepared candidate bundle",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle")
    source.add_argument("--loose", help="directory of <candidate_id>.png loose frames "
                                        "(needs --candidates)")
    parser.add_argument("--candidates", help="with --loose: JSON naming the frames")
    parser.add_argument("--out", required=True)
    parser.add_argument("--bundle-id", default=None)
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    args = parser.parse_args(argv)
    if args.loose and not args.candidates:
        parser.error("--loose needs --candidates")
    run_classification(
        Path(args.bundle) if args.bundle else None, Path(args.out),
        bundle_id=args.bundle_id,
        loose=(Path(args.loose), Path(args.candidates)) if args.loose else None,
        device=args.device,
    )
    print("ANGLE_CLASSIFICATION_COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BundleValidationError, OSError, ValueError, RuntimeError) as error:
        print(f"classify-angle: {error}", file=sys.stderr)
        sys.exit(1)
