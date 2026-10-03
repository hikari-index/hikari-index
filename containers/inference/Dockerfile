# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6

# The analyze workers' base: CUDA torch, transformers, timm and
# the anime face detector, with every model the analyze stage runs pinned by
# commit and checked by sha256, offline. Nothing else: the analyze workers
# (containers/rtx-worker builds FROM it; containers/cpu-worker copies its
# models) add the code they run.
# About 18 GB. The analyze Dockerfiles name it by the tag in their
# BASE_IMAGE / MODELS_IMAGE argument.

ARG PYTORCH_IMAGE="pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime@sha256:77f17f843507062875ce8be2a6f76aa6aa3df7f9ef1e31d9d7432f4b0f563dee"

FROM ${PYTORCH_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG HIKARI_UID=10001
ARG HIKARI_GID=10001
# SigLIP 2 is pinned to the commit the first build resolved from "main"
# (July 2026) and its
# checkpoint's sha256, the same pin as text-encoder and cpu-worker.
# Every stored vector came from these bytes; a rebuild that got different
# ones would silently change every new vector, so the build fails instead.
ARG SIGLIP_MODEL_ID="google/siglip2-base-patch16-224"
ARG SIGLIP_REVISION="75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2"
ARG SIGLIP_SHA256="612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b"
ARG WD_MODEL_ID="SmilingWolf/wd-swinv2-tagger-v3"
ARG WD_REVISION="627aef95638667ddcaa3ac8ae625e88ea5b02f51"
# The bytes those commits resolved to in the image every stored tag and face
# count came from (the same hashes containers/cpu-worker checks on its copy).
ARG WD_SHA256="39fe04000d5907a0a7616df54af744d43fbd70b5fba7abc3135d3675734c03ef"
ARG WD_TAGS_SHA256="298633d94d0031d2081c0893f29c82eab7f0df00b08483ba8f29d1e979441217"
ARG AFD_DETECTOR_SHA256="bf3093cc70aaeee6a0a45459aa5d724f9e74022b927b41ba364aac25adc8b009"
ARG AFD_LANDMARK_SHA256="e71271376406a743c01528a0460637fcc06e72aeeea583f85007cc72dc8b7a4a"
# anime-face-detector 0.1.0 declares requires-python >=3.12 but vendors the only
# mmdet/mmpose pieces it needs and runs on this image's 3.11 -- verified by
# import and by a real run. Pinned exactly so that stays a checked fact rather
# than an assumption a later release could quietly break. MIT, code and weights.
ARG AFD_VERSION="0.1.0"
ARG AFD_DETECTOR_REPO="hysts/anime-face-detector-faster-rcnn"
ARG AFD_DETECTOR_REVISION="f64ed6a45f6e0da561ee4e4e69f25e1d9a01fe55"
ARG AFD_LANDMARK_REPO="hysts/anime-face-detector-hrnetv2"
ARG AFD_LANDMARK_REVISION="9b3435248b26aeb82e2a8578fe9d86d5d57158af"

ENV DEBIAN_FRONTEND=noninteractive \
    HF_HUB_OFFLINE=0 \
    TRANSFORMERS_OFFLINE=0 \
    HF_HOME=/opt/hikari/models/hf-cache \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        file \
        git \
        jq \
        procps \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "transformers==4.51.3" \
        "timm==1.0.15" \
        "opencv-python-headless==5.0.0.93" \
        "pillow==11.0.0" \
        "sentencepiece==0.2.2" \
        "protobuf==7.35.1" \
        "huggingface_hub==0.36.2" \
        "hf-xet==1.5.2" \
        "tokenizers==0.21.4" \
        "safetensors==0.8.0" \
        "regex==2026.7.19" \
        "numpy==2.2.2" \
    && python -c "import torch, transformers; print('torch', torch.__version__); print('transformers', transformers.__version__); print('cuda', torch.version.cuda); print('cudnn', torch.backends.cudnn.version())" \
    && python -c "from transformers import Siglip2Model, Siglip2Processor; print('siglip2_classes_ok')" \
    && python -c "import timm; print('timm', timm.__version__)"

RUN set -eux; \
    test "${HIKARI_UID}" -ge 1000; \
    test "${HIKARI_GID}" -ge 1000; \
    existing_user="$(getent passwd "${HIKARI_UID}" | cut -d: -f1 || true)"; \
    if [ -n "${existing_user}" ]; then userdel --remove "${existing_user}" || userdel "${existing_user}"; fi; \
    existing_group="$(getent group "${HIKARI_GID}" | cut -d: -f1 || true)"; \
    if [ -n "${existing_group}" ]; then groupdel "${existing_group}"; fi; \
    groupadd --gid "${HIKARI_GID}" hikari; \
    useradd --uid "${HIKARI_UID}" --gid "${HIKARI_GID}" --create-home --shell /bin/bash hikari; \
    install --directory --owner hikari --group hikari \
        /opt/hikari/bin \
        /opt/hikari/models

# Fetch the public Apache-2.0 weights at the pinned commit, then check the
# checkpoint's bytes, so the run is offline and WEIGHTS_PIN.json names the
# exact revision without a run-time network call.
RUN python -c "from huggingface_hub import snapshot_download; import json, os; p = snapshot_download(repo_id=os.environ['SIGLIP_MODEL_ID'], revision=os.environ['SIGLIP_REVISION'], local_dir='/opt/hikari/models/siglip2-base-patch16-224', allow_patterns=['*.json','*.safetensors','*.txt','tokenizer*']); json.dump({'model_id': os.environ['SIGLIP_MODEL_ID'], 'revision': os.environ['SIGLIP_REVISION'], 'sha256': os.environ['SIGLIP_SHA256'], 'local_dir': p}, open('/opt/hikari/models/siglip2-base-patch16-224/WEIGHTS_PIN.json','w'), indent=2)" \
    && echo "${SIGLIP_SHA256}  /opt/hikari/models/siglip2-base-patch16-224/model.safetensors" | sha256sum -c -

# Pin the tagger beside SigLIP. The revision is a resolved commit rather than a
# branch, so the image cannot drift when upstream moves main. selected_tags.csv
# is fetched with the weights because its row order IS the output index -- a
# mismatched copy would silently relabel every prediction.
RUN python -c "from huggingface_hub import snapshot_download; import json, os; p = snapshot_download(repo_id=os.environ['WD_MODEL_ID'], revision=os.environ['WD_REVISION'], local_dir='/opt/hikari/models/wd-swinv2-tagger-v3', allow_patterns=['*.json','*.safetensors','*.csv']); json.dump({'model_id': os.environ['WD_MODEL_ID'], 'revision': os.environ['WD_REVISION'], 'local_dir': p}, open('/opt/hikari/models/wd-swinv2-tagger-v3/WEIGHTS_PIN.json','w'), indent=2)" \
    && echo "${WD_SHA256}  /opt/hikari/models/wd-swinv2-tagger-v3/model.safetensors" | sha256sum -c - \
    && echo "${WD_TAGS_SHA256}  /opt/hikari/models/wd-swinv2-tagger-v3/selected_tags.csv" | sha256sum -c -

# Face detector. Weights land in the Hub cache at pinned revisions so the run is
# offline; the package resolves them from that cache by repo id.
RUN pip install --no-cache-dir --ignore-requires-python "anime-face-detector==${AFD_VERSION}" \
    && python -c "import anime_face_detector; print('anime_face_detector import ok')" \
    && python -c "from huggingface_hub import hf_hub_download; import os; [hf_hub_download(r, 'model.safetensors', revision=v) for r, v in ((os.environ['AFD_DETECTOR_REPO'], os.environ['AFD_DETECTOR_REVISION']), (os.environ['AFD_LANDMARK_REPO'], os.environ['AFD_LANDMARK_REVISION']))]" \
    && echo "${AFD_DETECTOR_SHA256}  ${HF_HOME}/hub/models--hysts--anime-face-detector-faster-rcnn/snapshots/${AFD_DETECTOR_REVISION}/model.safetensors" | sha256sum -c - \
    && echo "${AFD_LANDMARK_SHA256}  ${HF_HOME}/hub/models--hysts--anime-face-detector-hrnetv2/snapshots/${AFD_LANDMARK_REVISION}/model.safetensors" | sha256sum -c - \
    && python -c "import json, os; json.dump({'package_version': os.environ['AFD_VERSION'], 'detector': {'repo': os.environ['AFD_DETECTOR_REPO'], 'revision': os.environ['AFD_DETECTOR_REVISION']}, 'landmark': {'repo': os.environ['AFD_LANDMARK_REPO'], 'revision': os.environ['AFD_LANDMARK_REVISION']}, 'licence': 'MIT (code and weights)'}, open('/opt/hikari/models/anime-face-detector-PIN.json', 'w'), indent=2)" \
    # Downloading by commit sha caches the snapshot but writes no refs/ entry,
    # and the package looks the weights up without a revision -- so an offline
    # run finds nothing. Point refs/main at the pinned sha: the pin stays exact
    # and the revision-less lookup resolves from cache.
    && python -c "import os, pathlib; [ (lambda d: (d.mkdir(parents=True, exist_ok=True), (d / 'main').write_text(v)))(pathlib.Path(os.environ['HF_HOME'], 'hub', 'models--' + r.replace('/', '--'), 'refs')) for r, v in ((os.environ['AFD_DETECTOR_REPO'], os.environ['AFD_DETECTOR_REVISION']), (os.environ['AFD_LANDMARK_REPO'], os.environ['AFD_LANDMARK_REVISION'])) ]"


# Every weight this image needs is now cached. Closing the Hub off makes a cache
# miss fail loudly instead of reaching the network: a run that can silently
# re-fetch is a run whose weights are not pinned.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PYTHONPATH=/opt/hikari/inference

RUN chown -R hikari:hikari /opt/hikari \
    && chmod 0555 /opt/hikari/bin \
    && chmod -R a+rX /opt/hikari/models

WORKDIR /opt/hikari
USER hikari

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD ["/bin/bash", "-c", "test \"$(id -u)\" -ne 0 && python -c 'import torch,transformers' && test -d /opt/hikari/models"]

ENTRYPOINT ["/usr/bin/tini", "--"]
# A base only: the analyze worker images set their own command.
CMD ["python", "-c", "import torch, transformers; print('inference base: torch', torch.__version__)"]