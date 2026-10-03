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

FROM ${RUST_IMAGE} AS palette
WORKDIR /build
COPY providers/palette-descriptor/Cargo.toml providers/palette-descriptor/Cargo.lock ./
COPY providers/palette-descriptor/src ./src
RUN rustup target add x86_64-unknown-linux-musl     && cargo build --release --target x86_64-unknown-linux-musl     && strip /build/target/x86_64-unknown-linux-musl/release/palette-descriptor

FROM ${BASE_IMAGE}
ARG PALETTE_SHA256
COPY --from=palette --chmod=0555 /build/target/x86_64-unknown-linux-musl/release/palette-descriptor /opt/hikari/bin/palette-descriptor
RUN echo "${PALETTE_SHA256}  /opt/hikari/bin/palette-descriptor" | sha256sum -c -

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
