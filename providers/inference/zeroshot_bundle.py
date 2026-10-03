"""Controlled zero-shot ranking over a prepared candidate bundle.

research/02 gives SigLIP "text/image retrieval and controlled zero-shot
ranking". Controlled means the label set is fixed to taxonomy values: this never
captions a frame and never invents a label.

SigLIP scores each image/text pair through a sigmoid rather than a softmax over
the candidate set, so labels do not compete for a fixed probability mass. That
is the right shape for animation, where a layout can legitimately read as two
things at once and a softmax would manufacture a winner. Ranking still happens,
but the margin between first and second is preserved as evidence rather than
normalised away, and a thin margin is what abstention is built on.

Raw scores only. Deciding which label wins and when to abstain is the
annotation layer's job, not this worker's -- the same split as the tagger.
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
    load_and_verify_bundle,
    peak_memory,
    resolve_device,
    sha256_bytes,
)

DEFAULT_MODEL_DIR = "/opt/hikari/models/siglip2-base-patch16-224"
PROMPTS_PATH = Path(__file__).with_name("zeroshot_prompts.json")
MODEL_ID = "google/siglip2-base-patch16-224"
PROVIDER_VERSION = "0.1.0"
TEXT_SEQUENCE_LENGTH = 64


def load_prompts(path: Path = PROMPTS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten(prompts: dict) -> tuple[list[str], list[tuple[str, str]]]:
    """Every prompt once, with a (family, label) index parallel to it."""
    texts: list[str] = []
    index: list[tuple[str, str]] = []
    for family, spec in prompts["families"].items():
        for label, phrasings in spec["labels"].items():
            for phrasing in phrasings:
                texts.append(phrasing)
                index.append((family, label))
    return texts, index


def run_zeroshot(
    bundle_dir: Path,
    out_dir: Path,
    model_dir: Path,
    device: str = "auto",
    bundle_id: Optional[str] = None,
) -> dict:
    import torch
    from PIL import Image
    from transformers import AutoModel, AutoProcessor

    manifest, manifest_sha, ready = load_and_verify_bundle(bundle_dir, bundle_id)
    bundle_digest = ready.get("bundle_digest")
    resolved_device = resolve_device(device)
    announce("zeroshot", resolved_device)
    started = time.perf_counter()
    prompts = load_prompts()
    texts, index = _flatten(prompts)

    processor = AutoProcessor.from_pretrained(str(model_dir))
    model = AutoModel.from_pretrained(
        str(model_dir), attn_implementation="sdpa"
    ).eval().to(resolved_device)

    # The text side is identical for every frame, so encode it once.
    # SigLIP2 expects a fixed 64-token sequence and its tokenizer declares no
    # default, so "max_length" padding silently does nothing without this.
    text_inputs = processor(
        text=texts, padding="max_length", max_length=TEXT_SEQUENCE_LENGTH,
        truncation=True, return_tensors="pt",
    ).to(resolved_device)
    with torch.inference_mode():
        text_features = model.get_text_features(**text_inputs)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    logit_scale = model.logit_scale.exp().item()
    logit_bias = model.logit_bias.item()

    results = []
    for candidate in manifest["candidates"]:
        path = bundle_dir / candidate["artifact_name"]
        with Image.open(path) as raw:
            image = raw.convert("RGB")
        image_inputs = processor(images=image, return_tensors="pt").to(resolved_device)
        with torch.inference_mode():
            image_features = model.get_image_features(**image_inputs)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            logits = (image_features @ text_features.T) * logit_scale + logit_bias
            scores = torch.sigmoid(logits)[0].float().cpu().tolist()

        # Average the paraphrases of each label; one phrasing is a coin flip.
        by_label: dict[str, dict[str, list[float]]] = {}
        for (family, label), score in zip(index, scores):
            by_label.setdefault(family, {}).setdefault(label, []).append(score)
        families = {
            family: {
                label: round(sum(values) / len(values), 8)
                for label, values in labels.items()
            }
            for family, labels in by_label.items()
        }
        results.append({
            "candidate_id": candidate["candidate_id"],
            "shot_id": candidate["shot_id"],
            "families": families,
        })
        top = {f: max(v, key=v.get) for f, v in families.items()}
        print(f"[zeroshot] {candidate['candidate_id']}: "
              + ", ".join(f"{f}={l}" for f, l in sorted(top.items())), flush=True)

    result_manifest = {
        "schema_version": "1.0",
        "provider": "siglip-zeroshot",
        "provider_version": PROVIDER_VERSION,
        "model_id": MODEL_ID,
        "prompt_set_version": prompts["prompt_set_version"],
        "taxonomy_version": prompts["taxonomy_version"],
        "runtime": {
            **device_report(resolved_device),
            **peak_memory(resolved_device),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "candidates": len(results),
        },
        "device": resolved_device,
        "scoring": "sigmoid per image/text pair; labels do not compete",
        "decision_policy": "none here; ranking and abstention are the annotation layer's job",
        "input_bundle_digest": bundle_digest,
        "input_manifest_sha256": manifest_sha,
        "bundle_id": manifest["bundle_id"],
        "candidates": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(result_manifest, sort_keys=True, indent=2) + "\n").encode()
    (out_dir / "result-manifest.json").write_bytes(manifest_bytes)
    (out_dir / "RESULT_READY.json").write_bytes(
        (json.dumps({
            "schema_version": "1.0",
            "state": "ready",
            "provider": "siglip-zeroshot",
            "result_manifest_sha256": sha256_bytes(manifest_bytes),
        }, sort_keys=True, indent=2) + "\n").encode()
    )
    return result_manifest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="zeroshot-bundle",
        description="Controlled zero-shot ranking over a prepared candidate bundle",
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--bundle-id", default=None)
    args = parser.parse_args(argv)

    run_zeroshot(Path(args.bundle), Path(args.out), Path(args.model_dir),
                 device=args.device, bundle_id=args.bundle_id)
    print("ZEROSHOT_COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BundleValidationError, OSError, ValueError) as error:
        print(f"zeroshot-bundle: {error}", file=sys.stderr)
        sys.exit(1)
