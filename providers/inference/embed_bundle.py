"""Embed the frames of a prepared-candidate bundle with SigLIP 2.

This is the inference worker's entrypoint. It consumes an immutable
prepared-candidate bundle (schema 2.1 or 2.2) produced by the extraction
pipeline, verifies every candidate against the manifest, runs the pinned
SigLIP 2 image encoder, and writes one embedding artifact per candidate plus
a bound result manifest.

Bundle verification lives in importable functions with no torch dependency;
model work happens only inside ``run_embeddings``. The result layout matches
the accepted embedding-1 evidence shape: ``artifacts/<candidate_id>.embedding.json``,
``result-manifest.json``, ``RESULT_READY.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Optional

RESULT_SCHEMA_VERSION = "embedding-1"
DEFAULT_MODEL_DIR = "/opt/hikari/models/siglip2-base-patch16-224"
MODEL_ID = "google/siglip2-base-patch16-224"
PROCESSING_IDENTITY = "siglip2-base-224-embeddings-v1"
PREPROCESSING_IDENTITY = "siglip2-fixres-224-pad-max-length-64-v1"


class BundleValidationError(Exception):
    """The bundle on disk does not match its own manifest or READY binding."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def load_and_verify_bundle(bundle_dir: Path, expected_bundle_id: Optional[str] = None):
    """Load manifest.json and READY.json, verifying every binding.

    Returns ``(manifest, manifest_sha256, ready)``. Raises
    BundleValidationError on any mismatch; never partially succeeds.
    """
    manifest_path = bundle_dir / "manifest.json"
    ready_path = bundle_dir / "READY.json"
    if not manifest_path.is_file() or not ready_path.is_file():
        raise BundleValidationError(
            f"bundle at {bundle_dir} is missing manifest.json or READY.json"
        )

    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    ready = json.loads(ready_path.read_text())
    manifest_sha = sha256_bytes(manifest_bytes)

    # No list of accepted format versions: this stage reads each frame's id,
    # file name, size and checksum, and anything that carries those is
    # readable. A bundle without them is refused in words that say what to
    # do, not which protocol number was expected.
    candidates = manifest.get("candidates")
    needed = ("candidate_id", "artifact_name", "bytes", "sha256")
    if not isinstance(candidates, list) or any(
            not isinstance(c, dict) or any(k not in c for k in needed) for c in candidates):
        raise BundleValidationError(
            f"the bundle at {bundle_dir} (format {manifest.get('schema_version')!r}) does not list its "
            "frames with an id, a file name, a size and a checksum, so this worker cannot read it. "
            "It was written by a source worker newer or older than this analyze worker: update "
            "whichever is behind (Jobs shows each worker's version)."
        )

    declared_sha = ready.get("manifest_sha256")
    if manifest_sha != declared_sha:
        raise BundleValidationError(
            f"manifest sha256 mismatch: recomputed {manifest_sha}, "
            f"READY.json declares {declared_sha}"
        )

    if expected_bundle_id is not None and manifest.get("bundle_id") != expected_bundle_id:
        raise BundleValidationError(
            f"bundle_id mismatch: manifest says {manifest.get('bundle_id')!r}, "
            f"expected {expected_bundle_id!r}"
        )

    # Ids become file names in every result this stage writes, and a frame's
    # file must be a real file inside the bundle: a matching checksum says
    # the bytes are the listed ones, not that the list points somewhere it
    # should.
    root = bundle_dir.resolve()
    for name, what in [(manifest.get("bundle_id"), "the bundle id")] + [
            (c["candidate_id"], "a frame id") for c in candidates]:
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise BundleValidationError(
                f"{what} {name!r} cannot be used as a file name "
                "(letters, digits, dot, dash and underscore only)")
    for cand in candidates:
        artifact = bundle_dir / str(cand["artifact_name"])
        if artifact.is_symlink() or root not in artifact.resolve().parents:
            raise BundleValidationError(
                f"candidate {cand['candidate_id']}: {cand['artifact_name']} is a link or "
                "points outside the bundle"
            )
        if not artifact.is_file():
            raise BundleValidationError(
                f"candidate {cand['candidate_id']}: missing {cand['artifact_name']}"
            )
        raw = artifact.read_bytes()
        if len(raw) != cand["bytes"] or sha256_bytes(raw) != cand["sha256"]:
            raise BundleValidationError(
                f"candidate {cand['candidate_id']}: bytes/sha256 do not match manifest"
            )

    return manifest, manifest_sha, ready


def resolve_device(requested: str) -> str:
    import torch

    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("--device cuda requested but CUDA is not available")
    return requested


def device_report(resolved: str) -> dict:
    """Name the hardware that actually ran, not the hardware that was asked for.

    `device: "cuda"` alone leaves the obvious question open -- which card, and
    was it really used. This records what a maintainer would otherwise have to
    catch live in `nvidia-smi`, and it lands in the result manifest, so a run
    that silently fell back to CPU is visible after the fact.
    """
    import torch

    report = {
        "device": resolved,
        "torch_version": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
    }
    if resolved.startswith("cuda"):
        properties = torch.cuda.get_device_properties(0)
        report["gpu_name"] = properties.name
        report["gpu_total_memory_mb"] = round(properties.total_memory / 1048576)
        report["cuda_version"] = torch.version.cuda
    return report


def peak_memory(resolved: str) -> dict:
    """Peak GPU and host memory for the process, so 'tons of RAM' is a number."""
    report: dict = {}
    if resolved.startswith("cuda"):
        import torch

        report["peak_gpu_allocated_mb"] = round(
            torch.cuda.max_memory_allocated() / 1048576, 1)
        report["peak_gpu_reserved_mb"] = round(
            torch.cuda.max_memory_reserved() / 1048576, 1)
    try:
        import resource
    except ImportError:  # Windows host; the worker image is Linux.
        return report
    # ru_maxrss is kilobytes on Linux.
    report["peak_host_rss_mb"] = round(
        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    return report


def announce(tag: str, resolved: str) -> None:
    report = device_report(resolved)
    detail = report.get("gpu_name", "CPU only")
    print(f"[{tag}] device={resolved} ({detail}) torch={report['torch_version']}",
          flush=True)


def run_embeddings(
    manifest: dict,
    manifest_sha: str,
    ready: dict,
    bundle_dir: Path,
    out_dir: Path,
    model_dir: Path,
    device: str = "auto",
    replay_check: bool = False,
) -> dict:
    """Embed every candidate; write artifacts and manifests under out_dir."""
    import torch
    from PIL import Image
    from transformers import AutoModel, AutoProcessor

    device = resolve_device(device)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    processor = AutoProcessor.from_pretrained(str(model_dir))
    model = AutoModel.from_pretrained(str(model_dir), attn_implementation="sdpa").eval().to(device)

    weight_revision = "unpinned"
    pin_path = model_dir / "WEIGHTS_PIN.json"
    if pin_path.is_file():
        pin = json.loads(pin_path.read_text())
        weight_revision = pin.get("revision", weight_revision)

    def embed_all(tag: str) -> List[dict]:
        results = []
        for cand in manifest["candidates"]:
            path = bundle_dir / cand["artifact_name"]
            t0 = time.perf_counter()
            image = Image.open(path).convert("RGB")
            t1 = time.perf_counter()
            inputs = processor(
                images=[image],
                padding="max_length",
                max_length=64,
                truncation=True,
                return_tensors="pt",
            ).to(device)
            with torch.no_grad():
                emb = model.get_image_features(**inputs)
            t2 = time.perf_counter()
            emb_cpu = emb.detach().cpu().to(torch.float32)
            results.append(
                {
                    "candidate": cand,
                    "emb_list": emb_cpu.squeeze(0).tolist(),
                    "emb_bytes": emb_cpu.numpy().tobytes(),
                    "emb_norm": float(emb_cpu.norm().item()),
                    "decode_ms": round((t1 - t0) * 1000.0, 3),
                    "inference_ms": round((t2 - t1) * 1000.0, 3),
                }
            )
            print(
                f"[{tag}] {cand['candidate_id']}: decode={results[-1]['decode_ms']}ms "
                f"inference={results[-1]['inference_ms']}ms "
                f"dim={emb_cpu.shape[-1]} norm={results[-1]['emb_norm']:.6f}",
                flush=True,
            )
        return results

    peak_vram_mib = None
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats(device=0)
    first = embed_all("embed")
    if device == "cuda":
        peak_vram_mib = round(torch.cuda.max_memory_allocated(device=0) / (1024 * 1024), 3)

    replay_identical = None
    if replay_check:
        second = embed_all("replay")
        replay_identical = all(
            a["emb_bytes"] == b["emb_bytes"] for a, b in zip(first, second)
        )
        print(f"replay_identical={replay_identical}", flush=True)

    bundle_id = manifest["bundle_id"]
    started = os.environ.get("HIKARI_RUN_STARTED") or time.strftime(
        "%Y%m%dT%H%M%SZ", time.gmtime()
    )
    result_id = f"{bundle_id}-siglip2-base-224-embeddings-{started}"

    candidate_results = []
    for r in first:
        cand_id = r["candidate"]["candidate_id"]
        artifact_name = f"artifacts/{cand_id}.embedding.json"
        payload = {
            "candidate_id": cand_id,
            "embedding": r["emb_list"],
            "embedding_dim": len(r["emb_list"]),
            "embedding_norm": r["emb_norm"],
            "decode_ms": r["decode_ms"],
            "inference_ms": r["inference_ms"],
        }
        artifact_bytes = (
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        (out_dir / artifact_name).write_bytes(artifact_bytes)
        candidate_results.append(
            {
                "candidate_id": cand_id,
                "status": "success",
                "artifact_name": artifact_name,
                "sha256": sha256_bytes(artifact_bytes),
                "bytes": len(artifact_bytes),
                "decode_ms": r["decode_ms"],
                "inference_ms": r["inference_ms"],
                "embedding_dim": len(r["emb_list"]),
                "embedding_norm": float(f"{r['emb_norm']:.6f}"),
            }
        )

    import transformers

    result_manifest = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_id": result_id,
        "bundle_id": bundle_id,
        "input_manifest_sha256": manifest_sha,
        "input_bundle_digest": ready.get("bundle_digest"),
        "processing_identity": PROCESSING_IDENTITY,
        "preprocessing_identity": PREPROCESSING_IDENTITY,
        "model": {
            "model_id": MODEL_ID,
            "license": "Apache-2.0",
            "weight_revision": weight_revision,
            "attn_implementation": "sdpa",
            "embedding_dim": candidate_results[0]["embedding_dim"] if candidate_results else None,
        },
        "environment": {
            "transformers_version": transformers.__version__,
            "torch_version": torch.__version__,
            "device": device,
            "cuda_version": torch.version.cuda if device == "cuda" else None,
        },
        "runtime": {
            "gpu_name": torch.cuda.get_device_name(0) if device == "cuda" else None,
            "peak_vram_mib": peak_vram_mib,
        },
        "determinism": {
            "replayed": replay_check,
            "identical": replay_identical,
        },
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "candidates": candidate_results,
    }

    manifest_out = out_dir / "result-manifest.json"
    manifest_bytes = (
        json.dumps(result_manifest, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    manifest_out.write_bytes(manifest_bytes)

    ready_out = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "result_id": result_id,
        "bundle_id": bundle_id,
        "result_manifest_sha256": sha256_bytes(manifest_bytes),
        "input_manifest_sha256": manifest_sha,
        "input_bundle_digest": ready.get("bundle_digest"),
        "model_id": MODEL_ID,
        "weight_revision": weight_revision,
        "state": "ready",
    }
    (out_dir / "RESULT_READY.json").write_bytes(
        (json.dumps(ready_out, sort_keys=True, indent=2) + "\n").encode("utf-8")
    )
    return result_manifest


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="embed-bundle",
        description="Embed a prepared-candidate bundle's frames with SigLIP 2",
    )
    parser.add_argument("--bundle", required=True,
                        help="prepared bundle directory (contains manifest.json)")
    parser.add_argument("--out", required=True, help="result output directory")
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--bundle-id", default=None,
                        help="optional expected bundle_id to verify against the manifest")
    parser.add_argument("--replay-check", action="store_true",
                        help="run inference twice and compare embeddings byte-for-byte")
    args = parser.parse_args(argv)

    bundle_dir = Path(args.bundle)
    try:
        manifest, manifest_sha, ready = load_and_verify_bundle(
            bundle_dir, expected_bundle_id=args.bundle_id
        )
    except BundleValidationError as exc:
        print(f"BUNDLE_INVALID: {exc}", file=sys.stderr)
        return 1

    print(
        f"bundle {manifest['bundle_id']} verified: "
        f"{len(manifest.get('candidates', []))} candidates, "
        f"schema {manifest.get('schema_version')}",
        flush=True,
    )

    run_embeddings(
        manifest,
        manifest_sha,
        ready,
        bundle_dir,
        Path(args.out),
        Path(args.model_dir),
        device=args.device,
        replay_check=args.replay_check,
    )
    print("EMBEDDING_COMPLETE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
