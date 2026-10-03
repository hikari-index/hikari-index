"""Run the WD tagger over a prepared candidate bundle.

Reuses the embedder's bundle verification: the bundle is immutable, read-only,
and checksum-bound, and this worker holds no write authority over the share.

Character predictions are dropped at the source. The protocol requires named
character and identity outputs to be discarded, so this module never writes them
anywhere -- not to the result, not to a private audit file. Filtering them
downstream would leave a window where a record could carry them; refusing to
emit them closes it. The checkpoint's 2,751 character logits are sliced out
before anything is serialised.

Rating predictions are kept but segregated. They are the sensitive-content lane
and are never discovery facets, so they live under their own key with their own
review expectations rather than mixed into the general tag set.
"""

from __future__ import annotations

import argparse
import hashlib
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
from .embed_loose import loose_inputs

DEFAULT_MODEL_DIR = "/opt/hikari/models/wd-swinv2-tagger-v3"
MODEL_ID = "SmilingWolf/wd-swinv2-tagger-v3"
PROVIDER_VERSION = "0.1.0"

# Danbooru category codes as they appear in selected_tags.csv.
CATEGORY_GENERAL = 0
CATEGORY_CHARACTER = 4
CATEGORY_RATING = 9

# This is a RETENTION floor, not a decision cut: it controls which scores the
# manifest stores, and the annotation fusion applies its own cuts downstream
# (0.35 base, 0.2653 for scene fields as of taxonomy v3). It sat at 0.35 until
# 2026-07-26, which made every manifest unable to support a lower fusion cut --
# calibration against reviewed frames needed a full re-tag purely to see below the floor.
# 0.05 keeps that from recurring; the scenery lane and all fusion decisions
# honor their configured thresholds regardless of what is retained.
DEFAULT_THRESHOLD = 0.05


def load_tag_index(model_dir: Path) -> dict:
    """Row order in selected_tags.csv is the model's output index."""
    import csv

    general, character, rating = [], [], []
    names = []
    with (model_dir / "selected_tags.csv").open(encoding="utf-8") as source:
        for index, row in enumerate(csv.DictReader(source)):
            names.append(row["name"])
            category = int(row["category"])
            if category == CATEGORY_GENERAL:
                general.append(index)
            elif category == CATEGORY_CHARACTER:
                character.append(index)
            elif category == CATEGORY_RATING:
                rating.append(index)
    return {
        "names": names,
        "general": general,
        "character": character,
        "rating": rating,
    }


def prepare_image(image, target_px: int):
    """Pad to a white square, then hand off to the model's own transform.

    Padding rather than cropping is what the reference implementation does, and
    it matters here: a centre crop would silently discard the edges of a 16:9
    frame, which is where composition lives.
    """
    from PIL import Image

    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGBA") if "transparency" in image.info else image.convert("RGB")
    if image.mode == "RGBA":
        canvas = Image.new("RGBA", image.size, (255, 255, 255))
        canvas.alpha_composite(image)
        image = canvas.convert("RGB")
    width, height = image.size
    side = max(width, height)
    canvas = Image.new("RGB", (side, side), (255, 255, 255))
    canvas.paste(image, ((side - width) // 2, (side - height) // 2))
    return canvas


def run_tagging(
    bundle_dir: Path,
    out_dir: Path,
    model_dir: Path,
    device: str = "auto",
    threshold: float = DEFAULT_THRESHOLD,
    bundle_id: Optional[str] = None,
    features_out: Optional[Path] = None,
    loose: Optional[tuple[Path, Path]] = None,
) -> dict:
    """loose = (frames dir, candidates file): tag named loose frames instead
    of a bundle (the picked surplus; see embed_loose.loose_inputs)."""
    import timm
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from safetensors.torch import load_file
    from timm.data import create_transform

    items, source = _inputs(bundle_dir, bundle_id, loose)
    resolved_device = resolve_device(device)
    announce("tag", resolved_device)
    started = time.perf_counter()
    index = load_tag_index(model_dir)

    # Built entirely from the pinned local files. `timm.create_model("hf-hub:…")`
    # reaches for the Hub even when the weights are already on disk, which fails
    # in an offline image -- correctly, since a run that can silently re-fetch is
    # a run whose weights are not pinned.
    config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
    model = timm.create_model(
        config["architecture"],
        pretrained=False,
        num_classes=config["num_classes"],
        **config.get("model_args", {}),
    )
    model.load_state_dict(load_file(str(model_dir / "model.safetensors")))
    model.eval().to(resolved_device)

    pretrained_cfg = config["pretrained_cfg"]
    target_px = pretrained_cfg["input_size"][-1]
    transform = create_transform(
        input_size=tuple(pretrained_cfg["input_size"]),
        interpolation=pretrained_cfg["interpolation"],
        mean=tuple(pretrained_cfg["mean"]),
        std=tuple(pretrained_cfg["std"]),
        crop_pct=pretrained_cfg["crop_pct"],
        is_training=False,
    )
    if len(index["names"]) != config["num_classes"]:
        raise BundleValidationError(
            f"selected_tags.csv has {len(index['names'])} rows but the model has "
            f"{config['num_classes']} outputs; the tag index would be wrong"
        )

    results = []
    feature_rows = []
    for candidate in items:
        with Image.open(candidate["path"]) as raw:
            padded = prepare_image(raw, target_px)
        tensor = transform(padded).unsqueeze(0)
        # The reference implementation feeds BGR; timm's transform emits RGB.
        tensor = tensor[:, [2, 1, 0]].to(resolved_device)
        with torch.inference_mode():
            # forward_features + forward_head IS model(tensor) for timm models,
            # split so the penultimate (pre-classifier) vector is reachable for
            # the research/02 WD-feature similarity comparison without a second
            # forward pass. The tag output is byte-identical either way; the
            # first --features-out run was diffed against the previous
            # manifests to prove it.
            feature_map = model.forward_features(tensor)
            probabilities = F.sigmoid(model.forward_head(feature_map))[0].float().cpu()
            if features_out is not None:
                penultimate = model.forward_head(
                    feature_map, pre_logits=True)[0].float().cpu()
                feature_rows.append((candidate["candidate_id"], penultimate))

        general = {
            index["names"][i]: round(float(probabilities[i]), 6)
            for i in index["general"]
            if float(probabilities[i]) >= threshold
        }
        rating = {
            index["names"][i]: round(float(probabilities[i]), 6)
            for i in index["rating"]
        }
        # Character logits are never read. See the module docstring.
        results.append({
            "candidate_id": candidate["candidate_id"],
            "shot_id": candidate["shot_id"],
            "general_tags": general,
            "rating": rating,
            "general_tags_above_threshold": len(general),
        })
        print(f"[tag] {candidate['candidate_id']}: {len(general)} general tags "
              f"at >= {threshold}", flush=True)

    result_manifest = {
        "schema_version": "1.0",
        "provider": "wd-tagger",
        "provider_version": PROVIDER_VERSION,
        "model_id": MODEL_ID,
        "model_revision": _pinned_revision(model_dir),
        "runtime": {
            **device_report(resolved_device),
            **peak_memory(resolved_device),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "candidates": len(results),
        },
        "device": resolved_device,
        "threshold": threshold,
        "threshold_status": "retention floor; the annotation fusion applies the decision cuts",
        **source,
        "character_predictions": "never read; dropped at the source per protocol",
        "rating_handling": "segregated sensitive-content lane, never a discovery facet",
        "candidates": results,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(result_manifest, sort_keys=True, indent=2) + "\n").encode()
    (out_dir / "result-manifest.json").write_bytes(manifest_bytes)
    (out_dir / "RESULT_READY.json").write_bytes(
        (json.dumps({
            "schema_version": "1.0",
            "state": "ready",
            "provider": "wd-tagger",
            "result_manifest_sha256": sha256_bytes(manifest_bytes),
        }, sort_keys=True, indent=2) + "\n").encode()
    )

    if features_out is not None:
        _write_features(features_out, feature_rows, result_manifest)
    return result_manifest


def _inputs(bundle_dir: Optional[Path], bundle_id: Optional[str],
            loose: Optional[tuple[Path, Path]]) -> tuple[list[dict], dict]:
    """Candidates with their frame paths, and what the result records about
    its input: the verified bundle's digests, or the loose files read."""
    if loose:
        return loose_inputs(*loose)
    manifest, manifest_sha, ready = load_and_verify_bundle(bundle_dir, bundle_id)
    items = [{**c, "path": bundle_dir / c["artifact_name"]} for c in manifest["candidates"]]
    return items, {"input_bundle_digest": ready.get("bundle_digest"),
                   "input_manifest_sha256": manifest_sha,
                   "bundle_id": manifest["bundle_id"]}


def _write_features(features_out: Path, feature_rows, tag_manifest: dict) -> None:
    """Penultimate-feature artifacts, mirroring embed_bundle's layout so the
    retrieval code loads every embedding family the same way."""
    artifacts = features_out / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    candidate_results = []
    for candidate_id, vector in feature_rows:
        values = [round(float(v), 6) for v in vector.tolist()]
        payload = {
            "candidate_id": candidate_id,
            "embedding": values,
            "embedding_dim": len(values),
            "embedding_norm": round(float(vector.norm().item()), 6),
        }
        artifact_name = f"artifacts/{candidate_id}.wdfeature.json"
        blob = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (features_out / artifact_name).write_bytes(blob)
        candidate_results.append({
            "candidate_id": candidate_id,
            "status": "success",
            "artifact_name": artifact_name,
            "sha256": sha256_bytes(blob),
            "bytes": len(blob),
            "embedding_dim": len(values),
        })
    manifest = {
        "schema_version": "wd-feature-1",
        "provider": "wd-tagger-features",
        "provider_version": PROVIDER_VERSION,
        "model_id": MODEL_ID,
        "model_revision": tag_manifest["model_revision"],
        "feature": "penultimate (forward_head pre_logits) of the tagger backbone",
        "input_bundle_digest": tag_manifest.get("input_bundle_digest"),
        "input_manifest_sha256": tag_manifest.get("input_manifest_sha256"),
        "bundle_id": tag_manifest["bundle_id"],
        "candidates": candidate_results,
    }
    blob = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (features_out / "result-manifest.json").write_bytes(blob)
    (features_out / "RESULT_READY.json").write_bytes(
        (json.dumps({
            "schema_version": "wd-feature-1",
            "state": "ready",
            "provider": "wd-tagger-features",
            "result_manifest_sha256": sha256_bytes(blob),
        }, sort_keys=True, indent=2) + "\n").encode()
    )


def _pinned_revision(model_dir: Path) -> str:
    pin = model_dir / "WEIGHTS_PIN.json"
    if pin.is_file():
        return json.loads(pin.read_text(encoding="utf-8")).get("revision", "unknown")
    return "unknown"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tag-bundle",
        description="Run the WD tagger over a prepared candidate bundle",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle")
    source.add_argument("--loose", help="directory of <candidate_id>.png loose frames "
                                        "(needs --candidates)")
    parser.add_argument("--candidates", help="with --loose: JSON naming the frames to tag")
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--bundle-id", default=None)
    parser.add_argument("--features-out", default=None,
                        help="also write the backbone's penultimate feature "
                             "per candidate (embed_bundle-shaped artifacts) "
                             "for the WD-feature similarity comparison")
    args = parser.parse_args(argv)
    if args.loose and not args.candidates:
        parser.error("--loose needs --candidates")

    run_tagging(
        Path(args.bundle) if args.bundle else None, Path(args.out), Path(args.model_dir),
        device=args.device, threshold=args.threshold, bundle_id=args.bundle_id,
        features_out=Path(args.features_out) if args.features_out else None,
        loose=(Path(args.loose), Path(args.candidates)) if args.loose else None,
    )
    print("TAGGING_COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BundleValidationError, OSError, ValueError) as error:
        print(f"tag-bundle: {error}", file=sys.stderr)
        sys.exit(1)
