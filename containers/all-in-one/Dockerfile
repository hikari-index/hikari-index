# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
#
# Hikari Index in one container: the gallery, its database (PostgreSQL 18
# with pgvector), the worker that reads your video and the text encoder
# for mood search. An install option for a host that manages single
# containers (an unRAID template, for one); the separate images and
# compose.yaml stay the reference install. The analyze worker is not in
# here: it is the heavy part, and runs as its own container (the cpu-worker
# image, or a GPU worker on another machine).
#
# Nothing is rebuilt here. The gallery, the worker and the text encoder are
# the files of their own images, copied whole, so frames, web images and
# phrase vectors are theirs byte for byte. The three images come in as
# named build contexts:
#
#   docker buildx bake -f docker-bake.hcl all-in-one    # all four, from this checkout
#
#   docker build -f containers/all-in-one/Dockerfile \
#     --build-context gallery=docker-image://ghcr.io/hikari-index/gallery:<version> \
#     --build-context worker=docker-image://ghcr.io/hikari-index/worker:<version> \
#     --build-context text-encoder=docker-image://ghcr.io/hikari-index/text-encoder:<version> \
#     --build-arg HIKARI_GALLERY_VERSION=gallery-<version> -t <name>:<version> .
#
# What it gives up against the separate containers: it starts as root (to
# make its folders and take the user id you give it), so it does not run
# read-only or with every capability dropped; each program then runs as
# that user. The database listens inside the container only.
#
# Mounts: /config (settings it makes, the database, database dumps),
# /data (run records and original frames), /images (web images),
# /source read-only (your video). Settings: HIKARI_ORIGIN (required),
# PUID and PGID, and the gallery's and worker's optional ones
# (https://docs.hikari-index.moe/reference/settings/).

ARG S6_OVERLAY_VERSION="3.2.3.2"
ARG S6_OVERLAY_NOARCH_SHA256="5379750ed30a84bbd2e2dd74847ba6b5bd29cd0b2e3ea2ec58049b57eb2eda12"
ARG S6_OVERLAY_X86_64_SHA256="e6befcc96a437a3831386ecfc51808c5d3e939dc5fe3c02ae9284599e8aa2408"

FROM gallery AS gallery-image
FROM text-encoder AS text-encoder-image

FROM worker

ARG DEBIAN_FRONTEND=noninteractive
ARG S6_OVERLAY_VERSION
ARG S6_OVERLAY_NOARCH_SHA256
ARG S6_OVERLAY_X86_64_SHA256

USER root

# PostgreSQL 18 and pgvector from the PostgreSQL project's own package
# repository. The minor version is whatever 18.x is current at build, as
# the compose install's `pg18` image tag is; `postgres --version` in the
# image says which. The en_US.UTF-8 locale is the one the official
# postgres image creates its databases with, so text sorts the same here.
RUN set -eux; \
    apt-get update; \
    apt-get install --yes --no-install-recommends locales tzdata xz-utils; \
    localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8; \
    install --directory /usr/share/postgresql-common/pgdg /etc/postgresql-common; \
    python -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc; \
    echo 'deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' \
        > /etc/apt/sources.list.d/pgdg.list; \
    echo 'create_main_cluster = false' > /etc/postgresql-common/createcluster.conf; \
    apt-get update; \
    apt-get install --yes --no-install-recommends postgresql-18 postgresql-18-pgvector; \
    rm -rf /var/lib/apt/lists/*; \
    /usr/lib/postgresql/18/bin/postgres --version; \
    test -f /usr/share/postgresql/18/extension/vector.control

# s6-overlay starts the programs in order, restarts one that dies and
# stops them in order (ISC licence). The exact release by sha256.
RUN set -eux; \
    cd /tmp; \
    for part in noarch x86_64; do \
        python -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' \
            "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${part}.tar.xz" \
            "s6-overlay-${part}.tar.xz"; \
    done; \
    echo "${S6_OVERLAY_NOARCH_SHA256}  s6-overlay-noarch.tar.xz" | sha256sum --check --strict -; \
    echo "${S6_OVERLAY_X86_64_SHA256}  s6-overlay-x86_64.tar.xz" | sha256sum --check --strict -; \
    tar -C / -Jxpf s6-overlay-noarch.tar.xz; \
    tar -C / -Jxpf s6-overlay-x86_64.tar.xz; \
    rm s6-overlay-noarch.tar.xz s6-overlay-x86_64.tar.xz

# The gallery: its Node runtime and its built app, as the gallery image
# has them.
COPY --from=gallery-image /usr/local/bin/node /usr/local/bin/node
COPY --from=gallery-image --chown=root:root /app /opt/hikari/gallery

# The text encoder: its Python 3.11 (the worker's is 3.12; both live under
# /usr/local without touching each other), its packages, the model and the
# code, as the text-encoder image has them. Only the venv's `python` link
# changes: there it pointed at /usr/local/bin/python, which here is 3.12.
COPY --from=text-encoder-image /usr/local/bin/python3.11 /usr/local/bin/python3.11
COPY --from=text-encoder-image /usr/local/lib/python3.11 /usr/local/lib/python3.11
COPY --from=text-encoder-image /usr/local/lib/libpython3.11.so.1.0 /usr/local/lib/libpython3.11.so.1.0
COPY --from=text-encoder-image /opt/venv /opt/venv
COPY --from=text-encoder-image /opt/hikari/models/siglip2-text /opt/hikari/models/siglip2-text
COPY --from=text-encoder-image /opt/hikari/inference /opt/hikari/inference
RUN set -eux; \
    ldconfig; \
    ln --symbolic --force /usr/local/bin/python3.11 /opt/venv/bin/python; \
    /opt/venv/bin/python -c 'import sys, torch, transformers; assert sys.version_info[:2] == (3, 11), sys.version; print(torch.__version__, transformers.__version__)'; \
    python -c 'import sys, cv2, scenedetect; assert sys.version_info[:2] == (3, 12), sys.version'; \
    node --version

# The start-up scripts, and the list of programs s6 runs: name, kind, then
# what must be up first.
COPY --chmod=0755 containers/all-in-one/scripts/ /opt/hikari/aio/
RUN set -eux; \
    ! grep -rlq "$(printf '\r')" /opt/hikari/aio \
        || { echo 'the all-in-one scripts have CRLF line endings; check the files out with LF' >&2; exit 1; }; \
    d=/etc/s6-overlay/s6-rc.d; \
    mkdir -p /etc/s6-overlay/user-bundles.d/user/contents.d; \
    echo bundle > /etc/s6-overlay/user-bundles.d/user/type; \
    svc() { \
        name="$1"; kind="$2"; shift 2; \
        mkdir -p "$d/$name/dependencies.d"; \
        echo "$kind" > "$d/$name/type"; \
        touch "/etc/s6-overlay/user-bundles.d/user/contents.d/$name" "$d/$name/dependencies.d/base"; \
        for dep in "$@"; do touch "$d/$name/dependencies.d/$dep"; done; \
        if [ "$kind" = oneshot ]; then echo "/opt/hikari/aio/$name" > "$d/$name/up"; \
        else ln --symbolic "/opt/hikari/aio/$name" "$d/$name/run"; fi; \
    }; \
    svc init-hikari oneshot; \
    svc postgres longrun init-hikari; \
    svc init-db oneshot postgres; \
    svc gallery longrun init-db; \
    svc worker longrun gallery; \
    svc text-encoder longrun init-hikari; \
    svc backup longrun init-db; \
    echo SIGINT > "$d/postgres/down-signal"; \
    echo 60000 > "$d/worker/timeout-kill"; \
    echo 10000 > "$d/gallery/timeout-kill"; \
    echo 5000 > "$d/text-encoder/timeout-kill"; \
    echo 5000 > "$d/backup/timeout-kill"

# Stopping: the worker gets a minute to hand its stage back, the gallery
# ten seconds, and only then is the database asked to shut down (SIGINT,
# its "fast" shutdown; it is never killed from in here). Give the
# container two minutes to stop (`--stop-timeout 120`) so Docker does not
# cut that short.

# PYTHONPATH is each program's own business here (the worker's and the
# encoder's differ), so the worker image's value is cleared.
ENV PYTHONPATH="" \
    LANG=en_US.UTF-8 \
    LC_ALL=en_US.UTF-8 \
    PUID=10001 \
    PGID=10001 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_VERBOSITY=1

# The image's own tag, passed at build; last, so a new version changes no
# layer above it. The worker's version comes with the worker image.
ARG HIKARI_GALLERY_VERSION
RUN test -n "$HIKARI_GALLERY_VERSION" || { echo 'pass --build-arg HIKARI_GALLERY_VERSION=<tag>' >&2; exit 1; }
ENV HIKARI_GALLERY_VERSION=${HIKARI_GALLERY_VERSION}

EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:3000/series').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
WORKDIR /
ENTRYPOINT ["/init"]
CMD []
