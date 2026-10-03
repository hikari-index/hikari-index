"""Embed a directory of loose candidate PNGs with SigLIP 2.

The surplus lane: frames the pipeline measured but did not publish are kept
as `surplus/<bundle_id>/<candidate_id>.png` with their audit records, and the
representative-set optimizer needs their embeddings to choose from the whole
measured pool. Surplus is not a checksum-bound bundle, so this provider
records what it read (per-file sha256) instead of verifying a manifest that
does not exist. Same artifact shape as embed_bundle so downstream loaders
need no second code path.

Committed rather than run as a one-off script: FACTS records exactly one
prior case of real inference living only in an uncommitted heredoc, and the
cost of that.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from .embed_bundle import (DEFAULT_MODEL_DIR, MODEL_ID, announce,
                           resolve_device, sha256_bytes)

RESULT_SCHEMA_VERSION = "loose-embedding-1"
PROCESSING_IDENTITY = "siglip2-base-224-embeddings-v1"
PREPROCESSING_IDENTITY = "siglip2-fixres-224-pad-max-length-64-v1"


def loose_inputs(src_dir: Path, candidates_file: Path) -> tuple[list[dict], dict]:
    """Named loose frames as provider inputs, for the tagger and face detector.

    The picked surplus lane: after the picker chooses from the whole pool, the
    few surplus frames it took get the same labels as bundle frames (ADR-0008
    amendment, 2026-09-28). `candidates_file` is JSON `{"bundle_id": ...,
    "candidates": [{"candidate_id", "shot_id", ...}]}`; each frame is
    `<src_dir>/<candidate_id>.png`. There is no bundle manifest to verify, so
    what was read is recorded per file, as embed_loose does.
    """
    document = json.loads(candidates_file.read_text(encoding="utf-8"))
    items, read = [], {}
    for candidate in document["candidates"]:
        cid = candidate["candidate_id"]
        path = src_dir / f"{cid}.png"
        if not path.is_file():
            raise ValueError(f"no loose frame {path.name} under {src_dir}")
        read[cid] = sha256_bytes(path.read_bytes())
        items.append({**candidate, "path": path})
    if not items:
        raise ValueError(f"{candidates_file} names no candidates")
    provenance = {"bundle_id": document["bundle_id"], "loose_dir": src_dir.name,
                  "source_sha256": read}
    return items, provenance


def run(src_dir: Path, out_dir: Path, model_dir: Path, device: str = "auto",
        note: str = "") -> dict:
    import torch
    from PIL import Image
    from transformers import AutoModel, AutoProcessor

    files = sorted(src_dir.glob("*.png"))
    if not files:
        raise ValueError(f"no PNGs under {src_dir}")
    device = resolve_device(device)
    announce("embed-loose", device)

    processor = AutoProcessor.from_pretrained(str(model_dir))
    model = AutoModel.from_pretrained(
        str(model_dir), attn_implementation="sdpa").eval().to(device)
    weight_revision = "unpinned"
    pin = model_dir / "WEIGHTS_PIN.json"
    if pin.is_file():
        weight_revision = json.loads(pin.read_text()).get("revision", weight_revision)

    artifacts = out_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    started_ts = time.perf_counter()
    results = []
    for f in files:
        cid = f.stem
        raw = f.read_bytes()
        image = Image.open(f).convert("RGB")
        inputs = processor(images=[image], padding="max_length", max_length=64,
                           truncation=True, return_tensors="pt").to(device)
        with torch.no_grad():
            emb = model.get_image_features(**inputs)
        vec = emb.detach().cpu().to(torch.float32).squeeze(0)
        payload = {
            "candidate_id": cid,
            "embedding": vec.tolist(),
            "embedding_dim": vec.shape[-1],
            "embedding_norm": float(vec.norm().item()),
            "source_file": f.name,
            "source_sha256": sha256_bytes(raw),
        }
        name = f"artifacts/{cid}.embedding.json"
        blob = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (out_dir / name).write_bytes(blob)
        results.append({"candidate_id": cid, "artifact_name": name,
                        "sha256": sha256_bytes(blob), "bytes": len(blob)})
        print(f"[embed-loose] {cid}", flush=True)

    import transformers
    manifest = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "weight_revision": weight_revision,
        "processing_identity": PROCESSING_IDENTITY,
        "preprocessing_identity": PREPROCESSING_IDENTITY,
        "source_dir_note": note or str(src_dir.name),
        "environment": {
            "transformers_version": transformers.__version__,
            "torch_version": torch.__version__,
            "device": device,
        },
        "runtime": {"elapsed_seconds": round(time.perf_counter() - started_ts, 3),
                    "candidates": len(results)},
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidates": results,
    }
    blob = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (out_dir / "result-manifest.json").write_bytes(blob)
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="embed-loose",
        description="Embed a directory of loose candidate PNGs with SigLIP 2")
    parser.add_argument("--dir", required=True,
                        help="directory of <candidate_id>.png files")
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--note", default="",
                        help="free-text provenance note recorded in the manifest")
    args = parser.parse_args(argv)
    run(Path(args.dir), Path(args.out), Path(args.model_dir),
        device=args.device, note=args.note)
    print("LOOSE_EMBEDDING_COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"embed-loose: {error}", file=sys.stderr)
        sys.exit(1)
