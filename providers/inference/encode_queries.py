"""Encode retrieval query texts with the pinned SigLIP 2 text tower.

The text half of the retrieval evaluation: query texts must be encoded by the
SAME model whose image vectors are stored, or the cosine space is meaningless
(research/02). Input is a JSON file with a list of {query_id, text} objects
(extra fields ignored); output mirrors embed_bundle's artifact shape keyed by
query_id.

The tokenizer trap is load-bearing and recorded in FACTS: SigLIP2 declares no
default max length, so padding="max_length" silently does nothing without an
explicit max_length=64 — and the stored image vectors were produced under
exactly that preprocessing identity.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List, Optional

from .embed_bundle import announce, resolve_device, sha256_bytes

RESULT_SCHEMA_VERSION = "query-text-embedding-1"
DEFAULT_MODEL_DIR = "/opt/hikari/models/siglip2-base-patch16-224"
MODEL_ID = "google/siglip2-base-patch16-224"
PROCESSING_IDENTITY = "siglip2-base-224-text-embeddings-v1"
PREPROCESSING_IDENTITY = "siglip2-tokenizer-pad-max-length-64-v1"


def run(queries_path: Path, out_dir: Path, model_dir: Path,
        device: str = "auto") -> dict:
    import torch
    from transformers import AutoModel, AutoProcessor

    device = resolve_device(device)
    announce("query-text", device)
    doc = json.loads(queries_path.read_text(encoding="utf-8"))
    queries = [q for q in (doc if isinstance(doc, list) else doc.get("text", doc))
               if isinstance(q, dict) and q.get("text")]
    if not queries:
        raise ValueError(f"no text queries found in {queries_path}")

    processor = AutoProcessor.from_pretrained(str(model_dir))
    model = AutoModel.from_pretrained(str(model_dir)).eval().to(device)

    weight_revision = "unpinned"
    pin = model_dir / "WEIGHTS_PIN.json"
    if pin.is_file():
        weight_revision = json.loads(pin.read_text()).get("revision", weight_revision)

    artifacts = out_dir / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    results = []
    for q in queries:
        inputs = processor(
            text=[q["text"]],
            padding="max_length",
            max_length=64,
            truncation=True,
            return_tensors="pt",
        ).to(device)
        with torch.no_grad():
            emb = model.get_text_features(**inputs)
        vec = emb.detach().cpu().to(torch.float32).squeeze(0)
        payload = {
            "query_id": q["query_id"],
            "text": q["text"],
            "embedding": vec.tolist(),
            "embedding_dim": vec.shape[-1],
            "embedding_norm": float(vec.norm().item()),
        }
        name = f"artifacts/{q['query_id']}.textembedding.json"
        blob = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (out_dir / name).write_bytes(blob)
        results.append({"query_id": q["query_id"], "artifact_name": name,
                        "sha256": sha256_bytes(blob), "bytes": len(blob)})
        print(f"[query-text] {q['query_id']}: dim={vec.shape[-1]}", flush=True)

    manifest = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "weight_revision": weight_revision,
        "processing_identity": PROCESSING_IDENTITY,
        "preprocessing_identity": PREPROCESSING_IDENTITY,
        "queries": results,
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    blob = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    (out_dir / "result-manifest.json").write_bytes(blob)
    return manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="encode-queries",
        description="Encode retrieval query texts with the pinned SigLIP 2 text tower")
    parser.add_argument("--queries", required=True,
                        help="JSON with a list (or {'text': [...]}) of "
                             "{query_id, text} objects")
    parser.add_argument("--out", required=True)
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    args = parser.parse_args(argv)
    run(Path(args.queries), Path(args.out), Path(args.model_dir), args.device)
    print("QUERY_ENCODING_COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"encode-queries: {error}", file=sys.stderr)
        sys.exit(1)
