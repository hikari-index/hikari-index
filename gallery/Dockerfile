# syntax=docker/dockerfile:1.19@sha256:b6afd42430b15f2d2a4c5a02b919e98a525b785b1aaff16747d2f623364e39b6
#
# Deployment image for the gallery (ADR-0007 SvelteKit, built as a plain
# Node server). Carries only the built app, its runtime dependencies, the
# database migrations the app applies at startup (ADR-0010). No source tree, no dev dependencies, no
# repository mount. Runs as a non-root user; the web images folder is mounted
# read-only at /images, and the only thing it writes on disk is the analyze
# records under /runs.
#
# Runtime environment (see gallery/.env.example): DATABASE_URL,
# HIKARI_IMAGES (=/images here), HIKARI_ADMIN_USER,
# HIKARI_ADMIN_PASSWORD_HASH, HIKARI_SESSION_SECRET, ENCODER_URL (optional),
# HIKARI_SHOKO_BASE_URL + HIKARI_SHOKO_API_KEY_FILE (optional; the Onboard
# picker, with the non-admin key file mounted read-only), and ORIGIN: the
# URL the LAN reaches the gallery at, which the admin sign-in form needs to
# accept a POST. PORT defaults to 3000. HIKARI_RUNS (the run records,
# mounted read/write at /runs) turns on the import stage and the storing of
# analyze results; HIKARI_WORKER_TOKENS ("name:token,name:token", one per
# analyze worker machine) turns on the worker API they use. BODY_SIZE_LIMIT
# is 64M for a worker's results upload. HIKARI_ADMIN_LINK=0 hides the
# header's Admin link (a link only; the sign-in gate stands regardless).
#
# A read-only build (--build-arg HIKARI_READ_ONLY_BUILD=1) is the gallery
# as a viewer only, for a copy strangers can reach: the admin area and the
# worker API are left out of the build altogether (there is no sign-in page
# to find), and HIKARI_READ_ONLY=1 is set in the image, so it applies no
# migration, runs no import and writes nothing to the database (ADR-0015);
# that is fixed in the build and no setting at run time undoes it.
# It needs DATABASE_URL (a login that may only read is enough), the images
# at /images, and ENCODER_URL for mood search. The same switch,
# HIKARI_READ_ONLY=1, turns an ordinary image into a viewer at run time.

ARG NODE_IMAGE="node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c"

FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY vite.config.js jsconfig.json ./
COPY src ./src
COPY static ./static
ARG HIKARI_READ_ONLY_BUILD=""
RUN if [ -n "$HIKARI_READ_ONLY_BUILD" ]; then \
        rm -rf src/routes/admin src/routes/api \
        && printf 'export const READ_ONLY_BUILD = true;\n' > src/lib/server/build-flags.js; \
    fi
RUN npm run build

FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM ${NODE_IMAGE}
ARG HIKARI_UID=10001
ARG HIKARI_GID=10001
# The image's own tag, passed at build (--build-arg HIKARI_GALLERY_VERSION=<tag>).
ARG HIKARI_GALLERY_VERSION
RUN test -n "$HIKARI_GALLERY_VERSION" || { echo 'pass --build-arg HIKARI_GALLERY_VERSION=<tag>' >&2; exit 1; }
ENV HIKARI_GALLERY_VERSION=${HIKARI_GALLERY_VERSION}
ARG HIKARI_READ_ONLY_BUILD=""
ENV HIKARI_READ_ONLY=${HIKARI_READ_ONLY_BUILD:+1}
ENV NODE_ENV=production \
    PORT=3000 \
    HOST=0.0.0.0 \
    HIKARI_IMAGES=/images \
    BODY_SIZE_LIMIT=64M
RUN groupadd --gid "${HIKARI_GID}" hikari \
    && useradd --uid "${HIKARI_UID}" --gid "${HIKARI_GID}" --create-home --shell /usr/sbin/nologin hikari \
    && install --directory --owner hikari --group hikari /app /images /share
WORKDIR /app
COPY --from=deps --chown=hikari:hikari /app/node_modules ./node_modules
COPY --from=build --chown=hikari:hikari /app/build ./build
COPY --chown=hikari:hikari package.json ./
COPY --chown=hikari:hikari migrations ./migrations
# first-run.js makes a new install's secrets (compose.yaml, README).
COPY --chown=hikari:hikari scripts/first-run.js ./scripts/first-run.js
USER hikari
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:'+(process.env.PORT||3000)+'/series').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"]
CMD ["node", "build"]
