# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
#
# The gallery's text encoder for mood search, slim and CPU-only, so it can run
# on a host with no GPU (research/02 option 2, "a small pinned-model service
# on the LAN"). Carries the SigLIP 2 TEXT tower only: the vision tower is
# dropped at build time, the phrase vector is unchanged (get_text_features is
# the text model's pooled output). Same code as the inference image's
# encode_server; no CUDA, no other models, no repository mount.
#
# The weights are pinned to the commit the inference image resolved in July
# (its WEIGHTS_PIN says "main"; the download metadata names the commit) and
# the full checkpoint is checked by sha256 before the text tower is cut out,
# so the vectors match the stored image vectors' space.
#
#   GET /health, GET /encode?text=..., POST /encode {"text": ...}  (port 5187)

ARG PYTHON_IMAGE="python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b"

FROM ${PYTHON_IMAGE} AS build
ARG SIGLIP_MODEL_ID="google/siglip2-base-patch16-224"
ARG SIGLIP_REVISION="75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
ARG SIGLIP_SHA256="612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b"
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# Versions match the inference image (torch 2.6.0, transformers 4.51.3) so the
# arithmetic is the same library; torch comes from the CPU-only wheel index.
# Pillow only because AutoProcessor loads the image processor beside the
# tokenizer; keeping the processor keeps the tokenizer call identical.
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.6.0+cpu" \
    && pip install \
        "transformers==4.51.3" \
        "tokenizers==0.21.4" \
        "safetensors==0.8.0" \
        "sentencepiece==0.2.2" \
        "protobuf==7.35.1" \
        "numpy==2.2.2" \
        "pillow==11.0.0" \
        "huggingface_hub==0.36.2"
RUN python - <<'EOF'
import hashlib, json, os, shutil
from huggingface_hub import snapshot_download
from transformers import Siglip2TextModel

full = "/tmp/siglip2-full"
out = "/opt/hikari/models/siglip2-text"
snapshot_download(repo_id=os.environ["SIGLIP_MODEL_ID"], revision=os.environ["SIGLIP_REVISION"],
                  local_dir=full, allow_patterns=["*.json", "*.safetensors", "*.txt", "tokenizer*"])
h = hashlib.sha256()
with open(f"{full}/model.safetensors", "rb") as f:
    for block in iter(lambda: f.read(1 << 20), b""):
        h.update(block)
if h.hexdigest() != os.environ["SIGLIP_SHA256"]:
    raise SystemExit(f"checkpoint sha256 {h.hexdigest()} != pinned {os.environ['SIGLIP_SHA256']}")
Siglip2TextModel.from_pretrained(full).save_pretrained(out)
for name in ("preprocessor_config.json", "special_tokens_map.json", "tokenizer.json",
             "tokenizer.model", "tokenizer_config.json"):
    shutil.copy(f"{full}/{name}", out)
json.dump({"model_id": os.environ["SIGLIP_MODEL_ID"], "revision": os.environ["SIGLIP_REVISION"],
           "checkpoint_sha256": os.environ["SIGLIP_SHA256"], "tower": "text"},
          open(f"{out}/WEIGHTS_PIN.json", "w"), indent=2)
shutil.rmtree(full)
# save_pretrained writes the weights 0600; the runtime user must read them.
for name in os.listdir(out):
    os.chmod(f"{out}/{name}", 0o644)
EOF

FROM ${PYTHON_IMAGE}
ARG HIKARI_UID=10001
ARG HIKARI_GID=10001
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/hikari/inference \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    OMP_NUM_THREADS=4 \
    LANG=C.UTF-8
RUN groupadd --gid "${HIKARI_GID}" hikari \
    && useradd --uid "${HIKARI_UID}" --gid "${HIKARI_GID}" --no-create-home --shell /usr/sbin/nologin hikari
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/hikari/models/siglip2-text /opt/hikari/models/siglip2-text
# Only the modules encode_server imports; the rest of the inference lane
# (tagger, faces, bundles) does not belong in this image.
COPY providers/inference/__init__.py providers/inference/embed_bundle.py \
     providers/inference/encode_queries.py providers/inference/encode_server.py \
     /opt/hikari/inference/inference/
# Permissions are set in the build stage: a chmod pass here would copy the
# 1.1 GB weights into a second layer.
USER hikari
EXPOSE 5187
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5187/health', timeout=5)"]
CMD ["python", "-m", "inference.encode_server", "--model-dir", "/opt/hikari/models/siglip2-text", "--device", "cpu", "--port", "5187"]
