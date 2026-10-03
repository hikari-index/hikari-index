"""A small HTTP service that encodes query text with the pinned SigLIP 2 text tower.

The gallery's mood search needs a text vector in the SAME space as the stored
image vectors (research/02: "a new phrase needs the compatible SigLIP text
encoder"; a different model's vectors cannot be compared). This wraps
encode_queries' exact preprocessing (max_length=64, the recorded trap) behind
one endpoint so the gallery never runs a model itself.

    GET  /health                 -> {"ok": true, "model_id": ..., "device": ...}
    GET  /encode?text=...        -> {"text", "embedding": [768 floats], "dim",
    POST /encode {"text": ...}      "model_id", "weight_revision",
                                    "preprocessing_identity", "ms"}

Standard library only, so it runs unchanged in the existing inference image.
Binds inside the container; the compose file publishes it on the host's
loopback. It is a LAN convenience, not a public surface: no auth, no rate
limit, input capped at 512 characters. A small in-process cache makes
repeated queries free.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .embed_bundle import resolve_device
from .encode_queries import DEFAULT_MODEL_DIR, MODEL_ID, PREPROCESSING_IDENTITY

MAX_TEXT = 512
CACHE_SIZE = 1024


class Encoder:
    def __init__(self, model_dir: Path, device: str):
        import torch
        from transformers import AutoModel, AutoProcessor

        self.torch = torch
        self.device = resolve_device(device)
        self.processor = AutoProcessor.from_pretrained(str(model_dir))
        # The slim encoder image ships the text tower alone (model_type
        # siglip2_text_model, which AutoModel does not map); its pooled output
        # IS get_text_features of the full model.
        if json.loads((model_dir / "config.json").read_text()).get("model_type") == "siglip2":
            model = AutoModel.from_pretrained(str(model_dir))
            self.features = model.get_text_features
        else:
            from transformers import Siglip2TextModel
            model = Siglip2TextModel.from_pretrained(str(model_dir))
            self.features = lambda **kw: model(**kw).pooler_output
        self.model = model.eval().to(self.device)
        self.weight_revision = "unpinned"
        pin = model_dir / "WEIGHTS_PIN.json"
        if pin.is_file():
            self.weight_revision = json.loads(pin.read_text()).get("revision", self.weight_revision)
        self.cache: "OrderedDict[str, list]" = OrderedDict()

    def encode(self, text: str) -> list:
        # The exact text: the tokenizer is case-sensitive, so a lowercased key
        # would hand "Rain" the vector made for "rain".
        key = text.strip()
        hit = self.cache.get(key)
        if hit is not None:
            self.cache.move_to_end(key)
            return hit
        inputs = self.processor(
            text=[text],
            padding="max_length",
            max_length=64,  # SigLIP2 declares no default; without this padding is a no-op
            truncation=True,
            return_tensors="pt",
        ).to(self.device)
        with self.torch.no_grad():
            emb = self.features(**inputs)
        vec = emb.detach().cpu().to(self.torch.float32).squeeze(0).tolist()
        self.cache[key] = vec
        if len(self.cache) > CACHE_SIZE:
            self.cache.popitem(last=False)
        return vec


def make_handler(encoder: Encoder):
    class Handler(BaseHTTPRequestHandler):
        server_version = "hikari-text-encoder/1"

        def _json(self, status: int, body: dict) -> None:
            blob = json.dumps(body, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def _encode(self, text: str) -> None:
            text = (text or "").strip()
            if not text:
                return self._json(400, {"error": "text is required"})
            if len(text) > MAX_TEXT:
                return self._json(413, {"error": f"text longer than {MAX_TEXT} characters"})
            started = time.perf_counter()
            vec = encoder.encode(text)
            self._json(200, {
                "text": text,
                "embedding": vec,
                "dim": len(vec),
                "model_id": MODEL_ID,
                "weight_revision": encoder.weight_revision,
                "preprocessing_identity": PREPROCESSING_IDENTITY,
                "device": encoder.device,
                "ms": round((time.perf_counter() - started) * 1000, 1),
            })

        def do_GET(self) -> None:  # noqa: N802 (http.server naming)
            url = urlparse(self.path)
            if url.path == "/health":
                return self._json(200, {"ok": True, "model_id": MODEL_ID, "device": encoder.device,
                                        "weight_revision": encoder.weight_revision})
            if url.path == "/encode":
                return self._encode(parse_qs(url.query).get("text", [""])[0])
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path != "/encode":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            if length > 4096:
                return self._json(413, {"error": "body too large"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"error": "body must be JSON"})
            self._encode(str(body.get("text", "")))

        def log_message(self, fmt: str, *args) -> None:
            # One line per request, no query text: a mood query is the user's business.
            sys.stderr.write(f"[text-encoder] {self.command} {urlparse(self.path).path} {args[1] if len(args) > 1 else ''}\n")

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="encode-server",
                                     description="Serve SigLIP 2 text embeddings over HTTP for the gallery's mood search")
    parser.add_argument("--model-dir", default=DEFAULT_MODEL_DIR)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5187)
    args = parser.parse_args(argv)

    started = time.perf_counter()
    encoder = Encoder(Path(args.model_dir), args.device)
    encoder.encode("warm-up")
    print(f"[text-encoder] ready on {args.host}:{args.port} device={encoder.device} "
          f"revision={encoder.weight_revision} load={time.perf_counter() - started:.1f}s", flush=True)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(encoder))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
