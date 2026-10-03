# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
#
# The analyze worker for a machine with no NVIDIA GPU: the same worker loop
# and providers as containers/rtx-worker, on CPU-only torch. Measured
# 2026-09-28: the CUDA image run on CPU gives the same picks, embeddings,
# tags, face counts and labels as on a GPU, so this image only drops the
# CUDA stack (about 4 GB of it) to make the image small enough for a
# small always-on machine.
#
# The model files are copied out of the local inference image rather than
# downloaded again, so they are the bytes every stored vector and label came
# from; each weight file is checked by sha256 below (SigLIP 2 at commit
# 75de2d55..., the text encoder's pin). Package versions match that image.
#
# Reaches the job table only through the gallery's worker API, like the GPU
# worker; reads the share read-only; writes nothing there.

ARG PYTHON_IMAGE="python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b"
ARG MODELS_IMAGE="hikari-index/inference:inference-d1f2552-20261002.1"
ARG RUST_IMAGE="rust:1.97-bookworm@sha256:77fac8b98f9f46062bb680b6d25d5bcaabfc400143952ebc572e924bcbedc3fa"

FROM ${MODELS_IMAGE} AS models
# The palette tool, built from the crate as containers/worker builds it;
# its sha256 is checked with the weights below.
FROM ${RUST_IMAGE} AS palette
WORKDIR /build
COPY providers/palette-descriptor/Cargo.toml providers/palette-descriptor/Cargo.lock ./
COPY providers/palette-descriptor/src ./src
RUN rustup target add x86_64-unknown-linux-musl     && cargo build --release --target x86_64-unknown-linux-musl     && strip /build/target/x86_64-unknown-linux-musl/release/palette-descriptor

FROM ${PYTHON_IMAGE} AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.6.0+cpu" "torchvision==0.21.0+cpu" \
    && pip install \
        "transformers==4.51.3" \
        "tokenizers==0.21.4" \
        "timm==1.0.15" \
        "safetensors==0.8.0" \
        "sentencepiece==0.2.2" \
        "protobuf==7.35.1" \
        "numpy==2.2.2" \
        "pillow==11.0.0" \
        "opencv-python-headless==5.0.0.93" \
        "huggingface_hub==0.36.2" \
    && pip install --no-deps --ignore-requires-python "anime-face-detector==0.1.0"

FROM ${PYTHON_IMAGE}
ARG HIKARI_UID=10001
ARG HIKARI_GID=10001
# The image's own tag, passed at build (--build-arg HIKARI_WORKER_VERSION=<tag>);
# the build fails without it, so no image reports "dev".
ARG HIKARI_WORKER_VERSION
RUN test -n "$HIKARI_WORKER_VERSION" || { echo 'pass --build-arg HIKARI_WORKER_VERSION=<tag>' >&2; exit 1; }
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/hikari/inference \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hikari/models/hf-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HIKARI_DEVICE=cpu \
    HIKARI_WORKER_ID=cpu-worker \
    HIKARI_WORKER_VERSION=${HIKARI_WORKER_VERSION} \
    LANG=C.UTF-8
RUN groupadd --gid "${HIKARI_GID}" hikari \
    && useradd --uid "${HIKARI_UID}" --gid "${HIKARI_GID}" --create-home --shell /usr/sbin/nologin hikari
COPY --from=build /opt/venv /opt/venv
COPY --from=models /opt/hikari/models/siglip2-base-patch16-224 /opt/hikari/models/siglip2-base-patch16-224
COPY --from=models /opt/hikari/models/wd-swinv2-tagger-v3 /opt/hikari/models/wd-swinv2-tagger-v3
COPY --from=models /opt/hikari/models/hf-cache/hub/models--hysts--anime-face-detector-faster-rcnn /opt/hikari/models/hf-cache/hub/models--hysts--anime-face-detector-faster-rcnn
COPY --from=models /opt/hikari/models/hf-cache/hub/models--hysts--anime-face-detector-hrnetv2 /opt/hikari/models/hf-cache/hub/models--hysts--anime-face-detector-hrnetv2
COPY --from=models /opt/hikari/models/anime-face-detector-PIN.json /opt/hikari/models/anime-face-detector-PIN.json
COPY --from=palette --chmod=0555 /build/target/x86_64-unknown-linux-musl/release/palette-descriptor /opt/hikari/bin/palette-descriptor
RUN cd /opt/hikari && sha256sum -c - <<'EOF'
612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b  models/siglip2-base-patch16-224/model.safetensors
39fe04000d5907a0a7616df54af744d43fbd70b5fba7abc3135d3675734c03ef  models/wd-swinv2-tagger-v3/model.safetensors
298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217  models/wd-swinv2-tagger-v3/selected_tags.csv
bf3093cc70aaeee6a0a45459aa5d724f9e74022b927b41ba364aac25adc8b009  models/hf-cache/hub/models--hysts--anime-face-detector-faster-rcnn/snapshots/f64ed6a45f6e0da561ee4e4e69f25e1d9a01fe55/model.safetensors
e71271376406a743c01528a0460637fcc06e72aeeea583f85007cc72dc8b7a4a  models/hf-cache/hub/models--hysts--anime-face-detector-hrnetv2/snapshots/9b3435248b26aeb82e2a8578fe9d86d5d57158af/model.safetensors
17c3c65a067fcabac46a8e47290dfa8a9400d6033179cbe28cacbe9f36ef948f  bin/palette-descriptor
EOF
COPY providers/inference /opt/hikari/inference/inference
RUN rm -f /opt/hikari/inference/inference/embed_dino.py /opt/hikari/inference/inference/zeroshot_bundle.py /opt/hikari/inference/inference/zeroshot_prompts.json
COPY providers/annotation /opt/hikari/inference/annotation
COPY providers/selection /opt/hikari/inference/selection
COPY worker/hikari_worker /opt/hikari/inference/hikari_worker
USER hikari
WORKDIR /home/hikari
HEALTHCHECK NONE
CMD ["python", "-m", "hikari_worker.analyze"]
