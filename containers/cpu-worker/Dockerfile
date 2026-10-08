# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
#
# The analyze worker for a machine with no NVIDIA GPU: the same worker loop
# and providers as containers/rtx-worker, on CPU-only torch. Measured
# 2026-09-28: the CUDA image run on CPU gives the same picks, embeddings,
# tags, face counts and labels as on a GPU, so this image only drops the
# CUDA stack (about 4 GB of it) to make the image small enough for a
# small always-on machine.
#
# The models are downloaded here at the same pinned commits as
# containers/inference, and each weight file is checked by sha256 against
# the bytes every stored vector and label came from (SigLIP 2 at commit
# 75de2d55..., the text encoder's pin). It no longer copies them out of the
# 18 GB CUDA base, so a machine with no GPU never builds CUDA. Package
# versions match that image.
#
# Reaches the job table only through the gallery's worker API, like the GPU
# worker; reads the share read-only; writes nothing there.

ARG PYTHON_IMAGE="python:3.11-slim-bookworm@sha256:a36c24f9cbdf4fd0f52d67f0823eeac19c2028c637cecc392d97f980d4fec56b"
ARG RUST_IMAGE="rust:1.97-bookworm@sha256:77fac8b98f9f46062bb680b6d25d5bcaabfc400143952ebc572e924bcbedc3fa"

# The model files, fetched at pinned commits (the same pins, and the same
# layout under /opt/hikari/models, as containers/inference).
FROM ${PYTHON_IMAGE} AS models
ARG SIGLIP_MODEL_ID="google/siglip2-base-patch16-224"
ARG SIGLIP_REVISION="75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
ARG WD_MODEL_ID="SmilingWolf/wd-swinv2-tagger-v3"
ARG WD_REVISION="627aef95638667ddcaa3ac8ae625e88ea5b02f51"
ARG AFD_VERSION="0.1.0"
ARG AFD_DETECTOR_REPO="hysts/anime-face-detector-faster-rcnn"
ARG AFD_DETECTOR_REVISION="f64ed6a45f6e0da561ee4e4e69f25e1d9a01fe55"
ARG AFD_LANDMARK_REPO="hysts/anime-face-detector-hrnetv2"
ARG AFD_LANDMARK_REVISION="9b3435248b26aeb82e2a8578fe9d86d5d57158af"
# The anime head and figure detectors (inference.detect_figures), MIT on
# their model cards; checked by sha256 in the final stage.
ARG DEEPGHS_HEAD_REVISION="06604feee81983792a57c21081e539c0ae229833"
ARG DEEPGHS_PERSON_REVISION="e39c744c22432ad01f91dd254fe2b02c8d878b8c"
# The camera-angle classifier (inference.classify_angle), Apache-2.0 on its
# model card; checked by sha256 in the final stage.
ARG ASLAKEY_ANGLE_REVISION="4de000c6e39630dbcdbe339401064f85274098e5"
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HF_HOME=/opt/hikari/models/hf-cache
RUN pip install "huggingface_hub==0.36.2" "hf-xet==1.5.2"
RUN python -c "from huggingface_hub import hf_hub_download; import json, os; d = '/opt/hikari/models/deepghs'; [hf_hub_download(r, f'{m}/model.onnx', revision=v, local_dir=d) for r, m, v in (('deepghs/anime_head_detection', 'head_detect_v2.0_s', os.environ['DEEPGHS_HEAD_REVISION']), ('deepghs/anime_person_detection', 'person_detect_v1.3_s', os.environ['DEEPGHS_PERSON_REVISION']))]; json.dump({'head': {'repo': 'deepghs/anime_head_detection', 'file': 'head_detect_v2.0_s/model.onnx', 'revision': os.environ['DEEPGHS_HEAD_REVISION']}, 'person': {'repo': 'deepghs/anime_person_detection', 'file': 'person_detect_v1.3_s/model.onnx', 'revision': os.environ['DEEPGHS_PERSON_REVISION']}, 'licence': 'MIT (model cards); trained with Ultralytics (AGPL-3.0)'}, open(d + '/PIN.json', 'w'), indent=2)" \
    && rm -rf /opt/hikari/models/deepghs/.cache
RUN python -c "from huggingface_hub import hf_hub_download; import json, os; d = '/opt/hikari/models/aslakey-camera-angle'; [hf_hub_download('aslakey/camera_angle', f, revision=os.environ['ASLAKEY_ANGLE_REVISION'], local_dir=d) for f in ('config.json', 'model.safetensors')]; json.dump({'repo': 'aslakey/camera_angle', 'revision': os.environ['ASLAKEY_ANGLE_REVISION'], 'base': 'facebook/dinov2-with-registers-large', 'licence': 'Apache-2.0 (model card; base model Apache-2.0)'}, open(d + '/PIN.json', 'w'), indent=2)" \
    && rm -rf /opt/hikari/models/aslakey-camera-angle/.cache
RUN python -c "from huggingface_hub import snapshot_download; import json, os; p = snapshot_download(repo_id=os.environ['SIGLIP_MODEL_ID'], revision=os.environ['SIGLIP_REVISION'], local_dir='/opt/hikari/models/siglip2-base-patch16-224', allow_patterns=['*.json','*.safetensors','*.txt','tokenizer*']); json.dump({'model_id': os.environ['SIGLIP_MODEL_ID'], 'revision': os.environ['SIGLIP_REVISION'], 'sha256': '612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b', 'local_dir': p}, open('/opt/hikari/models/siglip2-base-patch16-224/WEIGHTS_PIN.json','w'), indent=2)"
RUN python -c "from huggingface_hub import snapshot_download; import json, os; p = snapshot_download(repo_id=os.environ['WD_MODEL_ID'], revision=os.environ['WD_REVISION'], local_dir='/opt/hikari/models/wd-swinv2-tagger-v3', allow_patterns=['*.json','*.safetensors','*.csv']); json.dump({'model_id': os.environ['WD_MODEL_ID'], 'revision': os.environ['WD_REVISION'], 'local_dir': p}, open('/opt/hikari/models/wd-swinv2-tagger-v3/WEIGHTS_PIN.json','w'), indent=2)"
# Downloading by commit caches the snapshot but writes no refs/ entry, and
# the face detector looks its weights up without a revision; refs/main is
# pointed at the pinned commit so the offline lookup resolves.
RUN python -c "from huggingface_hub import hf_hub_download; import os; [hf_hub_download(r, 'model.safetensors', revision=v) for r, v in ((os.environ['AFD_DETECTOR_REPO'], os.environ['AFD_DETECTOR_REVISION']), (os.environ['AFD_LANDMARK_REPO'], os.environ['AFD_LANDMARK_REVISION']))]"     && python -c "import json, os; json.dump({'package_version': os.environ['AFD_VERSION'], 'detector': {'repo': os.environ['AFD_DETECTOR_REPO'], 'revision': os.environ['AFD_DETECTOR_REVISION']}, 'landmark': {'repo': os.environ['AFD_LANDMARK_REPO'], 'revision': os.environ['AFD_LANDMARK_REVISION']}, 'licence': 'MIT (code and weights)'}, open('/opt/hikari/models/anime-face-detector-PIN.json', 'w'), indent=2)"     && python -c "import os, pathlib; [ (lambda d: (d.mkdir(parents=True, exist_ok=True), (d / 'main').write_text(v)))(pathlib.Path(os.environ['HF_HOME'], 'hub', 'models--' + r.replace('/', '--'), 'refs')) for r, v in ((os.environ['AFD_DETECTOR_REPO'], os.environ['AFD_DETECTOR_REVISION']), (os.environ['AFD_LANDMARK_REPO'], os.environ['AFD_LANDMARK_REVISION'])) ]"

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
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/opt/hikari/inference \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/hikari/models/hf-cache \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HIKARI_DEVICE=cpu \
    HIKARI_WORKER_ID=cpu-worker \
    LANG=C.UTF-8
RUN groupadd --gid "${HIKARI_GID}" hikari \
    && useradd --uid "${HIKARI_UID}" --gid "${HIKARI_GID}" --create-home --shell /usr/sbin/nologin hikari
COPY --from=build /opt/venv /opt/venv
# The head and figure detectors' runtime (MIT), in a layer of its own so
# the venv layer above stays the same and an update does not download it.
RUN PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 pip install "onnxruntime==1.23.2"
COPY --from=models /opt/hikari/models/siglip2-base-patch16-224 /opt/hikari/models/siglip2-base-patch16-224
COPY --from=models /opt/hikari/models/wd-swinv2-tagger-v3 /opt/hikari/models/wd-swinv2-tagger-v3
COPY --from=models /opt/hikari/models/hf-cache/hub/models--hysts--anime-face-detector-faster-rcnn /opt/hikari/models/hf-cache/hub/models--hysts--anime-face-detector-faster-rcnn
COPY --from=models /opt/hikari/models/hf-cache/hub/models--hysts--anime-face-detector-hrnetv2 /opt/hikari/models/hf-cache/hub/models--hysts--anime-face-detector-hrnetv2
COPY --from=models /opt/hikari/models/anime-face-detector-PIN.json /opt/hikari/models/anime-face-detector-PIN.json
COPY --from=models /opt/hikari/models/deepghs /opt/hikari/models/deepghs
COPY --from=models /opt/hikari/models/aslakey-camera-angle /opt/hikari/models/aslakey-camera-angle
COPY --from=palette --chmod=0555 /build/target/x86_64-unknown-linux-musl/release/palette-descriptor /opt/hikari/bin/palette-descriptor
RUN cd /opt/hikari && sha256sum -c - <<'EOF'
612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b  models/siglip2-base-patch16-224/model.safetensors
39fe04000d5907a0a7616df54af744d43fbd70b5fba7abc3135d3675734c03ef  models/wd-swinv2-tagger-v3/model.safetensors
298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217  models/wd-swinv2-tagger-v3/selected_tags.csv
bf3093cc70aaeee6a0a45459aa5d724f9e74022b927b41ba364aac25adc8b009  models/hf-cache/hub/models--hysts--anime-face-detector-faster-rcnn/snapshots/f64ed6a45f6e0da561ee4e4e69f25e1d9a01fe55/model.safetensors
e71271376406a743c01528a0460637fcc06e72aeeea583f85007cc72dc8b7a4a  models/hf-cache/hub/models--hysts--anime-face-detector-hrnetv2/snapshots/9b3435248b26aeb82e2a8578fe9d86d5d57158af/model.safetensors
17c3c65a067fcabac46a8e47290dfa8a9400d6033179cbe28cacbe9f36ef948f  bin/palette-descriptor
6679f9b71192298bbf174d82e9e5581c3237b0c3dc67deace7cdbf686b070a00  models/deepghs/head_detect_v2.0_s/model.onnx
6da88929438cd442e31e45ff4f934dd2d7eb9cf7a423c22885d650ee52550f90  models/deepghs/person_detect_v1.3_s/model.onnx
b82275a3b1e909fb82eb55967e4a737b5296ae18c9cf1d9239fdc329d914460e  models/aslakey-camera-angle/model.safetensors
a04d559a00aca5dbaa6a27fe9e7dfbe16a420b074cb563eaee99facd1121fd43  models/aslakey-camera-angle/config.json
EOF
COPY providers/inference /opt/hikari/inference/inference
RUN rm -f /opt/hikari/inference/inference/embed_dino.py /opt/hikari/inference/inference/zeroshot_bundle.py /opt/hikari/inference/inference/zeroshot_prompts.json
COPY providers/annotation /opt/hikari/inference/annotation
COPY providers/selection /opt/hikari/inference/selection
COPY worker/hikari_worker /opt/hikari/inference/hikari_worker
USER hikari
WORKDIR /home/hikari

# The image's own tag, passed at build (--build-arg HIKARI_WORKER_VERSION=<tag>);
# the build fails without it, so no image reports "dev". Last, so a new
# version changes no layer above it and an update downloads only what changed.
ARG HIKARI_WORKER_VERSION
RUN test -n "$HIKARI_WORKER_VERSION" || { echo 'pass --build-arg HIKARI_WORKER_VERSION=<tag>' >&2; exit 1; }
ENV HIKARI_WORKER_VERSION=${HIKARI_WORKER_VERSION}

HEALTHCHECK NONE
CMD ["python", "-m", "hikari_worker.analyze"]
