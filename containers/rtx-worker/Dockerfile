# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
#
# The GPU analyze worker (ADR-0003, ADR-0004): the analyze stage. Built on
# the local inference base (containers/inference: CUDA torch, the
# pinned SigLIP 2, WD tagger and face-detector weights, offline), adding the
# current inference providers, the annotation fusion, the stratified picker
# (providers/selection, ADR-0008), the palette tool and the worker loop.
#
# Reads the run data read-only at /share; writes nothing there.
# Working files live in /tmp (a tmpfs); results go back through the
# gallery's worker API.

ARG BASE_IMAGE="hikari-index/inference:inference-d1f2552-20261002.1"
# The palette tool for the picked surplus frames, built from the crate the
# same way the worker image builds it (containers/worker), so its
# descriptors match the bundle's byte for byte. The sha256 is the 0.2.0
# binary every worker image has carried since 2026-09-30; the build fails
# if this one comes out different.
ARG RUST_IMAGE="rust:1.97-bookworm@sha256:77fac8b98f9f46062bb680b6d25d5bcaabfc400143952ebc572e924bcbedc3fa"
ARG PALETTE_SHA256="17c3c65a067fcabac46a8e47290dfa8a9400d6033179cbe28cacbe9f36ef948f"
# The anime head and figure detectors (inference.detect_figures), fetched
# here rather than in the base so an existing base keeps working: the same
# pins and sha256 as containers/cpu-worker. MIT on their model cards.
ARG PYTHON_IMAGE="python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b"
ARG DEEPGHS_HEAD_REVISION="06604feee81983792a57c21081e539c0ae229833"
ARG DEEPGHS_PERSON_REVISION="e39c744c22432ad01f91dd254fe2b02c8d878b8c"
ARG ASLAKEY_ANGLE_REVISION="4de000c6e39630dbcdbe339401064f85274098e5"

FROM ${PYTHON_IMAGE} AS figures
ARG DEEPGHS_HEAD_REVISION
ARG DEEPGHS_PERSON_REVISION
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "huggingface_hub==0.36.2" "hf-xet==1.5.2"
RUN python -c "from huggingface_hub import hf_hub_download; import json, os; d = '/opt/hikari/models/deepghs'; [hf_hub_download(r, f'{m}/model.onnx', revision=v, local_dir=d) for r, m, v in (('deepghs/anime_head_detection', 'head_detect_v2.0_s', os.environ['DEEPGHS_HEAD_REVISION']), ('deepghs/anime_person_detection', 'person_detect_v1.3_s', os.environ['DEEPGHS_PERSON_REVISION']))]; json.dump({'head': {'repo': 'deepghs/anime_head_detection', 'file': 'head_detect_v2.0_s/model.onnx', 'revision': os.environ['DEEPGHS_HEAD_REVISION']}, 'person': {'repo': 'deepghs/anime_person_detection', 'file': 'person_detect_v1.3_s/model.onnx', 'revision': os.environ['DEEPGHS_PERSON_REVISION']}, 'licence': 'MIT (model cards); trained with Ultralytics (AGPL-3.0)'}, open(d + '/PIN.json', 'w'), indent=2)" \
    && rm -rf /opt/hikari/models/deepghs/.cache \
    && cd /opt/hikari/models/deepghs && sha256sum -c - <<'EOF'
6679f9b71192298bbf174d82e9e5581c3237b0c3dc67deace7cdbf686b070a00  head_detect_v2.0_s/model.onnx
6da88929438cd442e31e45ff4f934dd2d7eb9cf7a423c22885d650ee52550f90  person_detect_v1.3_s/model.onnx
EOF

# The camera-angle classifier (inference.classify_angle), the same pin and
# sha256 as containers/cpu-worker. Apache-2.0 on its model card.
FROM ${PYTHON_IMAGE} AS angle
ARG ASLAKEY_ANGLE_REVISION
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "huggingface_hub==0.36.2" "hf-xet==1.5.2"
RUN python -c "from huggingface_hub import hf_hub_download; import json, os; d = '/opt/hikari/models/aslakey-camera-angle'; [hf_hub_download('aslakey/camera_angle', f, revision=os.environ['ASLAKEY_ANGLE_REVISION'], local_dir=d) for f in ('config.json', 'model.safetensors')]; json.dump({'repo': 'aslakey/camera_angle', 'revision': os.environ['ASLAKEY_ANGLE_REVISION'], 'base': 'facebook/dinov2-with-registers-large', 'licence': 'Apache-2.0 (model card; base model Apache-2.0)'}, open(d + '/PIN.json', 'w'), indent=2)" \
    && rm -rf /opt/hikari/models/aslakey-camera-angle/.cache \
    && cd /opt/hikari/models/aslakey-camera-angle && sha256sum -c - <<'EOF'
b82275a3b1e909fb82eb55967e4a737b5296ae18c9cf1d9239fdc329d914460e  model.safetensors
a04d559a00aca5dbaa6a27fe9e7dfbe16a420b074cb563eaee99facd1121fd43  config.json
EOF

FROM ${RUST_IMAGE} AS palette
WORKDIR /build
COPY providers/palette-descriptor/Cargo.toml providers/palette-descriptor/Cargo.lock ./
COPY providers/palette-descriptor/src ./src
RUN rustup target add x86_64-unknown-linux-musl     && cargo build --release --target x86_64-unknown-linux-musl     && strip /build/target/x86_64-unknown-linux-musl/release/palette-descriptor

FROM ${BASE_IMAGE}
ARG PALETTE_SHA256
COPY --from=palette --chmod=0555 /build/target/x86_64-unknown-linux-musl/release/palette-descriptor /opt/hikari/bin/palette-descriptor
RUN echo "${PALETTE_SHA256}  /opt/hikari/bin/palette-descriptor" | sha256sum -c -
COPY --from=figures --chown=hikari:hikari /opt/hikari/models/deepghs /opt/hikari/models/deepghs
COPY --from=angle --chown=hikari:hikari /opt/hikari/models/aslakey-camera-angle /opt/hikari/models/aslakey-camera-angle
USER root
RUN pip install --no-cache-dir --disable-pip-version-check "onnxruntime==1.23.2"
USER hikari

COPY --chown=hikari:hikari providers/inference /opt/hikari/inference/inference
RUN rm -f /opt/hikari/inference/inference/embed_dino.py /opt/hikari/inference/inference/zeroshot_bundle.py /opt/hikari/inference/inference/zeroshot_prompts.json
COPY --chown=hikari:hikari providers/annotation /opt/hikari/inference/annotation
COPY --chown=hikari:hikari providers/selection /opt/hikari/inference/selection
COPY --chown=hikari:hikari worker/hikari_worker /opt/hikari/inference/hikari_worker

# The image's own tag, passed at build (--build-arg HIKARI_WORKER_VERSION=<tag>);
# the build fails without it, so no image reports "dev".
ARG HIKARI_WORKER_VERSION
RUN test -n "$HIKARI_WORKER_VERSION" || { echo 'pass --build-arg HIKARI_WORKER_VERSION=<tag>' >&2; exit 1; }
ENV HIKARI_WORKER_VERSION=${HIKARI_WORKER_VERSION} \
    PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK NONE
CMD ["python", "-m", "hikari_worker.analyze"]
