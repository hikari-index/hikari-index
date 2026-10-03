"""Embed the frames of a prepared-candidate bundle with DINOv2-small.

The research/02 image-descriptor comparison's third candidate: image-only
structural/style similarity, evaluated against SigLIP and the WD tagger's
penultimate feature on identical queries. Maintainer-approved addition
2026-07-27; Apache-2.0 code and weights, pinned into the image like SigLIP.

Bundle verification and the artifact layout are embed_bundle's; only the
model differs. DINOv2 has no text tower, so this provider serves image
queries only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from .embed_bundle import (BundleValidationError, announce,
                           load_and_verify_bundle, resolve_device,
                           sha256_bytes)

RESULT_SCHEMA_VERSION = "dino-embedding-1"
DEFAULT_MODEL_DIR = "/opt/hikari/models/dinov2-small"
MODEL_ID = "facebook/dinov2-small"
PROCESSING_IDENTITY = "dinov2-small-cls-embeddings-v1"
PREPROCESSING_IDENTITY = "dinov2-processor-default-v1"


def run_embeddings(
    manifest: dict,
    manifest_sha: str,
    ready: dict,
    bundle_dir: Path,
    out_dir: Path,
    model_dir: Path,
    device: str = "auto",
) -> dict:
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModel

    device = resolve_device(device)
    announce("dino", device)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    processor = AutoImageProcessor.from_pretrained(str(model_dir))
    model = AutoModel.from_pretrained(str(model_dir)).eval().to(device)

    weight_revision = "unpinned"
    pin_path = model_dir / "WEIGHTS_PIN.json"
    if pin_path.is_file():
        weight_revision = json.loads(pin_path.read_text()).get(
            "revision", weight_revision)

    started_ts = time.perf_counter()
    candidate_results = []
    for cand in manifest["candidates"]:
        path = bundle_dir / cand["artifact_name"]
        t0 = time.perf_counter()
        image = Image.open(path).convert("RGB")
        t1 = time.perf_counter()
        inputs = processor(images=image, return_tensors="pt").to(device)
        with torch.no_grad():
            out = model(**inputs)
            # CLS token: the model card's designated global descriptor for
            # nearest-neighbour retrieval. pooler_output is CLS through an
            # extra untrained-for-retrieval layernorm path in some ports, so
            # take the hidden state directly.
            emb = out.last_hidden_state[:, 0, :]
        t2 = time.perf_counter()
        emb_cpu = emb.detach().cpu().to(torch.float32).squeeze(0)
        values = emb_cpu.tolist()
        payload = {
            "candidate_id": cand["candidate_id"],
            "embedding": values,
            "embedding_dim": len(values),
            "embedding_norm": float(emb_cpu.norm().item()),
            "decode_ms": round((t1 - t0) * 1000.0, 3),
            "inference_ms": round((t2 - t1) * 1000.0, 3),
        }
        artifact_name = f"artifacts/{cand['candidate_id']}.dino.json"
        blob = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (out_dir / artifact_name).write_bytes(blob)
        candidate_results.append({
            "candidate_id": cand["candidate_id"],
            "status": "success",
            "artifact_name": artifact_name,
            "sha256": sha256_bytes(blob),
            "bytes": len(blob),
            "embedding_dim": len(values),
        })
        print(f"[dino] {cand['candidate_id']}: dim={len(values)} "
              f"decode={payload['decode_ms']}ms inference={payload['inference_ms']}ms",
              flush=True)

    import transformers

    bundle_id = manifest["bundle_id"]
    started = os.environ.get("HIKARI_RUN_STARTED") or time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime())
    result_manifest = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_id": f"{bundle_id}-dinov2-small-embeddings-{started}",
        "bundle_id": bundle_id,
        "input_manifest_sha256": manifest_sha,
        "input_bundle_digest": ready.get("bundle_digest"),
        "processing_identity": PROCESSING_IDENTITY,
        "preprocessing_identity": PREPROCESSING_IDENTITY,
        "model": {
            "model_id": MODEL_ID,
            "license": "Apache-2.0",
            "weight_revision": weight_revision,
            "embedding": "CLS token of last_hidden_state",
            "embedding_dim": candidate_results[0]["embedding_dim"] if candidate_results else None,
        },
        "environment": {
            "transformers_version": transformers.__version__,
            "torch_version": __import__("torch").__version__,
            "device": device,
        },
        "runtime": {
            "elapsed_seconds": round(time.perf_counter() - started_ts, 3),
            "candidates": len(candidate_results),
        },
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidates": candidate_results,
    }
    blob = (json.dumps(result_manifest, sort_keys=True, indent=2) + "\n").encode()
    (out_dir / "result-manifest.json").write_bytes(blob)
    (out_dir / "RESULT_READY.json").write_bytes(
        (json.dumps({
            "schema_version": RESULT_SCHEMA_VERSION,
            "result_id": result_manifest["result_id"],
            "bundle_id": bundle_id,
            "result_manifest_sha256": sha256_bytes(blob),
            "input_manifest_sha256": manifest_sha,
            "input_bundle_digest": ready.get("bundle_digest"),
            "model_id": MODEL_ID,
            "weight_revision": weight_revision,
            "state": "ready",
        }, sort_keys=True, indent=2) + "\n").encode())
    return result_manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="embed-dino",
        description="Embed a prepared-candidate bundle's frames with DINOv2-small",
    )
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--bundle-id", default=None)
    args = parser.parse_args(argv)

    bundle_dir = Path(args.bundle)
    try:
        manifest, manifest_sha, ready = load_and_verify_bundle(
            bundle_dir, expected_bundle_id=args.bundle_id)
    except BundleValidationError as exc:
        print(f"BUNDLE_INVALID: {exc}", file=sys.stderr)
        return 1
    print(f"bundle {manifest['bundle_id']} verified: "
          f"{len(manifest.get('candidates', []))} candidates", flush=True)
    run_embeddings(manifest, manifest_sha, ready, bundle_dir,
                   Path(args.out), Path(args.model_dir), device=args.device)
    print("DINO_EMBEDDING_COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
