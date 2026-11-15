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
# Nothing is rebuilt here. This image starts from the worker image and
# copies in the gallery's built app and Node, and the text encoder's
# Python, packages, model and code, from their own images. The files are
# theirs; the operating system under the gallery and the text encoder is
# the worker image's, so equal output is something a run shows, not
# something the copy guarantees (docs/BUILDING.md). The three images come
# in as named build contexts:
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
# /source read-only (your video). Settings: the ones settings.json lists
# for the all-in-one, under the same names and with the same defaults as
# every other install (HIKARI_ORIGIN is required here), plus PUID and
# PGID. Each program is started with its own settings and nothing else
# (scripts/env, `program`).

ARG S6_OVERLAY_VERSION="3.2.3.2"
ARG S6_OVERLAY_NOARCH_SHA256="5379750ed30a84bbd2e2dd74847ba6b5bd29cd0b2e3ea2ec58049b57eb2eda12"
ARG S6_OVERLAY_X86_64_SHA256="e6befcc96a437a3831386ecfc51808c5d3e939dc5fe3c02ae9284599e8aa2408"

# THE database version is one line in compose.yaml: the pgvector image at
# a digest. This is that same image, used here only to read which
# PostgreSQL and pgvector it holds; the build stops if the digest below
# and compose.yaml's differ (docs/BUILDING.md, "The database version").
ARG DATABASE_IMAGE="pgvector/pgvector:pg18@sha256:38681dc6eaf55308e2a4c6de392d131652dcfac70e1263e35cd9b5ea3fe39782"

FROM ${DATABASE_IMAGE} AS database-reference
RUN set -eu; \
    mkdir /reference; \
    dpkg-query -W -f='${Version}' postgresql-18 > /reference/postgresql; \
    awk -F"'" '/^default_version/ { print $2 }' /usr/share/postgresql/18/extension/vector.control > /reference/pgvector; \
    test -s /reference/postgresql; \
    test -s /reference/pgvector; \
    echo "reference: PostgreSQL $(cat /reference/postgresql), pgvector $(cat /reference/pgvector)"

FROM gallery AS gallery-image
FROM text-encoder AS text-encoder-image

FROM worker

ARG DEBIAN_FRONTEND=noninteractive
ARG S6_OVERLAY_VERSION
ARG S6_OVERLAY_NOARCH_SHA256
ARG S6_OVERLAY_X86_64_SHA256

USER root

# PostgreSQL and pgvector at exactly the versions the reference image
# holds, installed the way each project documents: the PostgreSQL apt
# repository set up by its own script (the Quickstart at
# https://wiki.postgresql.org/wiki/Apt: install postgresql-common, run
# apt.postgresql.org.sh; it uses the signing key that package ships),
# then the server, its JIT package and client at the reference's package
# version (the same packages the postgres image installs, and the
# reference image is built on), and pgvector by its README's APT method
# at the reference's version (there it is compiled from the same
# release). The en_US.UTF-8 locale is the one the postgres image creates
# its databases with, so text sorts the same here.
ARG DATABASE_IMAGE
COPY --from=database-reference /reference /opt/hikari/database-reference
COPY compose.yaml compose.db.yaml /tmp/pin/
RUN set -eux; \
    for file in compose.yaml compose.db.yaml; do \
        named="$(awk '$1 == "image:" { for (i = 2; i <= NF; i++) if ($i ~ /^pgvector\//) print $i }' "/tmp/pin/$file")"; \
        test "$named" = "${DATABASE_IMAGE}" \
            || { echo "$file names the database image '$named' and this build names '${DATABASE_IMAGE}': the three must be the same, whole, digest included (docs/BUILDING.md, The database version)" >&2; exit 1; }; \
    done; \
    rm -r /tmp/pin; \
    pg="$(cat /opt/hikari/database-reference/postgresql)"; \
    vector="$(cat /opt/hikari/database-reference/pgvector)"; \
    apt-get update; \
    apt-get install --yes --no-install-recommends locales tzdata xz-utils postgresql-common; \
    localedef -i en_US -c -f UTF-8 -A /usr/share/locale/locale.alias en_US.UTF-8; \
    sed -ri 's/^#?(create_main_cluster) .*$/\1 = false/' /etc/postgresql-common/createcluster.conf; \
    grep -qx 'create_main_cluster = false' /etc/postgresql-common/createcluster.conf; \
    /usr/share/postgresql-common/pgdg/apt.postgresql.org.sh -y; \
    grep -q '^Suites: bookworm-pgdg$' /etc/apt/sources.list.d/pgdg.sources; \
    vector_package="$(apt-cache madison postgresql-18-pgvector | awk -v v="$vector" '$3 ~ "^" v "-" { print $3; exit }')"; \
    test -n "$vector_package" \
        || { echo "the PostgreSQL repository has no postgresql-18-pgvector $vector (the reference image's version)" >&2; exit 1; }; \
    apt-get install --yes --no-install-recommends \
        "postgresql-18=$pg" "postgresql-18-jit=$pg" "postgresql-client-18=$pg" "libpq5=$pg" \
        "postgresql-18-pgvector=$vector_package"; \
    rm -rf /var/lib/apt/lists/*; \
    test "$(dpkg-query -W -f='${Version}' postgresql-18)" = "$pg"; \
    test "$(awk -F"'" '/^default_version/ { print $2 }' /usr/share/postgresql/18/extension/vector.control)" = "$vector"; \
    /usr/lib/postgresql/18/bin/postgres --version

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
# The daily database dump: the same script compose.yaml's backup service
# runs.
COPY --chmod=0755 containers/dump/dump-daily /opt/hikari/dump-daily
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

# Stopping, in order: the worker gets up to a minute to hand its stage
# back, the gallery ten seconds, and then the database is asked to shut
# down (SIGINT, its "fast" shutdown). That needs the container to be
# given the time: start it with `--stop-timeout 120`, or Docker's own
# ten seconds cuts it short and kills what is still running.

# The locale and PYTHONPATH here stay the worker image's, and only the
# start scripts see them: each program gets its own environment from its
# start script, and the database the locale the postgres image uses.
ENV PUID=10001 \
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
# One container, one health: the gallery's check (its image's) and the
# text encoder's (its image's), so mood search being down shows here as
# it does on the text encoder's own container elsewhere.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD ["node", "-e", "Promise.all(['http://127.0.0.1:3000/series','http://127.0.0.1:5187/health'].map(u=>fetch(u))).then(rs=>process.exit(rs.every(r=>r.ok)?0:1)).catch(()=>process.exit(1))"]
WORKDIR /
ENTRYPOINT ["/init"]
CMD []
