# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
#
# The worker that opens the video library (ADR-0003, ADR-0011): it polls the
# gallery's job table and runs extraction (the frame-sample pipeline, the
# frame, PTS and colour reference of ADR-0006), the web images, and the
# file deletions. Built from this repository and public base images only.
#
#   docker build -f containers/worker/Dockerfile \
#     --build-arg HIKARI_WORKER_VERSION=<tag> -t <name>:<tag> .
#
# This replaced the build layered on the July frame-sample image on
# 2026-10-02, after a real episode extracted in it matched that image's
# output byte for byte (docs/BUILDING.md).
#
# What a rebuild must not change, and how each is held:
# - published pixels: jellyfin-ffmpeg 8.1.3-1, the exact package by sha256
#   (tone-mapped output differs between ffmpeg versions: 7.1's tonemapx
#   converted BT.2020 to BT.709 in place and pushed reds toward pure red;
#   8.1 is the first line without that. SDR output is pixel-identical on
#   both, and the pipeline sets the PNG prediction itself so the bytes
#   are too);
# - which frames are published: the Python packages in requirements.txt,
#   pinned to the set every v3 bundle was made with;
# - the palette descriptors: the crate with its Cargo.lock on a pinned Rust
#   image (the binary has come out byte-identical on every build so far);
# - the pipeline's recorded digest (sha256 of pipeline_cli.py): LF line
#   endings, checked below.
#
# Mounts: /source read-only (the library), /output/runs read-write (the run
# folders), /images read-write (the gallery's web images).

ARG PYTHON_IMAGE="python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
ARG RUST_IMAGE="rust:1.97-bookworm@sha256:77fac8b98f9f46062bb680b6d25d5bcaabfc400143952ebc572e924bcbedc3fa"
# The package is named by its major version (jellyfin-ffmpeg7, jellyfin-ffmpeg8).
# The Jellyfin project publishes no checksum for its packages: this one was
# computed from the release asset on 2026-10-03 (47,548,928 bytes).
ARG JELLYFIN_FFMPEG_MAJOR="8"
ARG JELLYFIN_FFMPEG_VERSION="8.1.3-1"
ARG JELLYFIN_FFMPEG_SHA256="4829b34df16843ecf1aca8b1a600874b5c767396e1d1990c889c761fd143d928"

FROM ${RUST_IMAGE} AS rust-build
WORKDIR /build
COPY providers/palette-descriptor/Cargo.toml providers/palette-descriptor/Cargo.lock ./
COPY providers/palette-descriptor/src ./src
RUN rustup target add x86_64-unknown-linux-musl \
    && cargo build --release --target x86_64-unknown-linux-musl \
    && strip /build/target/x86_64-unknown-linux-musl/release/palette-descriptor \
    && test "$(/build/target/x86_64-unknown-linux-musl/release/palette-descriptor --identity)" = "palette-descriptor-0.2.0"

FROM ${PYTHON_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG HIKARI_UID=10001
ARG HIKARI_GID=10001
ARG JELLYFIN_FFMPEG_MAJOR
ARG JELLYFIN_FFMPEG_VERSION
ARG JELLYFIN_FFMPEG_SHA256

# jellyfin-ffmpeg is the deliberate colour reference (it carries tonemapx
# with BT.2390). It is GPL v3 or later; its source is the tag
# v${JELLYFIN_FFMPEG_VERSION} of github.com/jellyfin/jellyfin-ffmpeg, and its
# copyright file is installed under /usr/share/doc/jellyfin-ffmpeg<major>.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        jq \
        libgl1 \
        libglib2.0-0 \
    && jellyfin_ffmpeg_deb="jellyfin-ffmpeg${JELLYFIN_FFMPEG_MAJOR}_${JELLYFIN_FFMPEG_VERSION}-bookworm_amd64.deb" \
    && python -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
        "https://github.com/jellyfin/jellyfin-ffmpeg/releases/download/v${JELLYFIN_FFMPEG_VERSION}/${jellyfin_ffmpeg_deb}" \
        "${jellyfin_ffmpeg_deb}" \
    && echo "${JELLYFIN_FFMPEG_SHA256}  ${jellyfin_ffmpeg_deb}" | sha256sum --check --strict - \
    && apt-get install -y "./${jellyfin_ffmpeg_deb}" \
    && rm "${jellyfin_ffmpeg_deb}" \
    && ln --symbolic /usr/lib/jellyfin-ffmpeg/ffmpeg /usr/local/bin/ffmpeg \
    && ln --symbolic /usr/lib/jellyfin-ffmpeg/ffprobe /usr/local/bin/ffprobe \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    ffmpeg_version="${JELLYFIN_FFMPEG_VERSION%-*}"; \
    ffmpeg -version | grep -Fq "${ffmpeg_version}-Jellyfin"; \
    ffmpeg -filters 2>&1 | grep -Eq '(^|[[:space:]])tonemapx([[:space:]]|$)'

RUN set -eux; \
    test "${HIKARI_UID}" -ge 1000; \
    test "${HIKARI_GID}" -ge 1000; \
    groupadd --gid "${HIKARI_GID}" hikari; \
    useradd --uid "${HIKARI_UID}" --gid "${HIKARI_GID}" --create-home --shell /bin/bash hikari

COPY containers/worker/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --no-deps -r /app/requirements.txt \
    && pip install --no-cache-dir --no-deps "opencv-python==5.0.0.93" \
    && python -c "import cv2, scenedetect, av, imagehash, pg8000.native, PIL.features as f; assert f.check('avif'); assert cv2.__version__ == '5.0.0'"

# The code, root-owned and read-only to the worker.
COPY providers/frame_sample/ /app/frame_sample/
COPY providers/web_derivatives/ /app/web_derivatives/
COPY worker/hikari_worker/ /app/hikari_worker/
COPY --from=rust-build --chmod=0555 /build/target/x86_64-unknown-linux-musl/release/palette-descriptor /opt/hikari/bin/palette-descriptor

# The pipeline records the sha256 of its own file in every bundle. A
# checkout with Windows line endings changes that digest with no change to
# the code (it happened once, caught before delivery): refuse to build.
RUN ! grep -q "$(printf '\r')" /app/frame_sample/pipeline_cli.py \
    || { echo 'pipeline_cli.py has CRLF line endings; check the files out with LF' >&2; exit 1; }

RUN install --directory --owner hikari --group hikari /output /source /images

WORKDIR /app
USER hikari
ENV PYTHONPATH="/app" \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8

# The source check needs MD4 for ED2K, which OpenSSL 3 keeps in its legacy
# module; fail the build, not every ED2K onboarding, if it cannot load.
RUN OPENSSL_CONF=/app/hikari_worker/openssl-legacy.cnf python -c "import hashlib; assert hashlib.new('md4', b'abc').hexdigest() == 'a448017aaf21d8525fc10ae87aa6729d'"

# The image's own tag, passed at build; the build fails without it, so no
# image reports "dev".
ARG HIKARI_WORKER_VERSION
RUN test -n "$HIKARI_WORKER_VERSION" || { echo 'pass --build-arg HIKARI_WORKER_VERSION=<tag>' >&2; exit 1; }
ENV HIKARI_WORKER_VERSION=${HIKARI_WORKER_VERSION}

ENTRYPOINT ["python", "-m", "hikari_worker"]
CMD []
