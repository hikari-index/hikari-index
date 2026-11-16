# Building the images

Every image builds from this repository and public base images. An
install normally pulls the published images instead (docs/RELEASING.md);
the GPU analyze worker is the one it always builds. `docker compose
build` builds everything from your checkout under the same names; the
commands below are for building one image by hand. Run the commands from the repository
root; the tag you pass is what the image reports as its version (the
worker and gallery builds refuse to run without one).

| Image | Dockerfile | Built on | Rough size |
|---|---|---|---|
| gallery | `gallery/Dockerfile` (context `gallery/`) | `node:22-bookworm-slim` | 0.4 GB |
| worker | `containers/worker/Dockerfile` | `python:3.12.13-slim-bookworm`, `rust:1.97-bookworm` | 1.7 GB |
| inference base | `containers/inference/Dockerfile` | `pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime` | 18 GB |
| analyze worker, NVIDIA GPU | `containers/rtx-worker/Dockerfile` | the inference base | 18 GB |
| analyze worker, CPU | `containers/cpu-worker/Dockerfile` | `python:3.11-slim-bookworm`, weights fetched at the inference base's pins | 6 GB |
| text encoder (mood search) | `containers/text-encoder/Dockerfile` | `python:3.11-slim-bookworm` | 3.9 GB |
| all-in-one (gallery, database, worker, text encoder) | `containers/all-in-one/Dockerfile` | the worker image, with the gallery's and text encoder's files copied in | 6.1 GB |

```bash
docker build --build-arg HIKARI_GALLERY_VERSION=<tag> -t hikari-index/gallery:<tag> gallery
docker build -f containers/worker/Dockerfile --build-arg HIKARI_WORKER_VERSION=<tag> -t hikari-index/worker:<tag> .
```

The GPU worker needs the inference base first, under the name its
Dockerfile expects (or pass your own with `--build-arg BASE_IMAGE=`):

```bash
docker build -f containers/inference/Dockerfile -t hikari-index/inference:inference-d1f2552-20261002.1 .
docker build -f containers/rtx-worker/Dockerfile --build-arg HIKARI_WORKER_VERSION=<tag> -t hikari-index/rtx-worker:<tag> .
```

The CPU worker stands alone:

```bash
docker build -f containers/cpu-worker/Dockerfile --build-arg HIKARI_WORKER_VERSION=<tag> -t hikari-index/cpu-worker:<tag> .
```

The all-in-one image is put together from the gallery, worker and text
encoder images rather than built again: it starts from the worker image
and copies in the gallery's built app and Node, and the text encoder's
Python, packages, model and code. The files are theirs; the operating
system under the gallery and the text encoder is the worker image's, so
that they give the same output is something only a run shows: put one
film through this image and through the compose install and compare the
frames and web images. Do it again when a base image moves; a build that
succeeds does not show it.

It adds PostgreSQL with pgvector at the versions the database image
holds (next section) and s6-overlay (the exact release by sha256) to
start and stop the parts in order. One command builds the three and then
the image:

```bash
VERSION=<version> docker buildx bake -f docker-bake.hcl all-in-one
```

Or from images you already have, as named build contexts (the header of
`containers/all-in-one/Dockerfile` has the full command).

## The database version

What this repository starts a database with is one image at one digest:
the `db` service in `compose.yaml`, `pgvector/pgvector:pg18` (PostgreSQL
18.7 and pgvector 0.8.7 since 2026-11). `compose.db.yaml`, the
development database, names the same image at the same digest.

The all-in-one image has no database image inside it: its Dockerfile
names the same image (`DATABASE_IMAGE`), reads the PostgreSQL package
version and the pgvector version out of it, and installs those from
packages. PostgreSQL comes the way its project documents (the apt
repository set up by `apt.postgresql.org.sh`; the server, JIT and client
packages are the ones the image itself is built from) and pgvector by
its README's APT method, so pgvector there is the same version from a
different build. The build stops unless both compose files name the
very same image, digest included, and when the package repository no
longer carries those versions. An all-in-one install gets a new pin with
the image, not with `git pull`.

An install gets a new pin when its copy of `compose.yaml` does, which is
the `git pull` in the docs' update steps; changing `HIKARI_VERSION` alone
leaves the database where it was. A container template you fill in
yourself is outside this file: one that names the image by its moving
tag (`pgvector/pgvector:pg18`, as the unRAID pgvector-18 app does) gets
whatever the tag holds when the container is updated, which can be a
minor version ahead of or behind the pin. To hold such a container to
the pin, put the image with its digest, exactly as `compose.yaml` has
it, in the template's image field.

To move to a newer database: `docker pull pgvector/pgvector:pg18`, read
its digest (`docker inspect --format '{{index .RepoDigests 0}}' pgvector/pgvector:pg18`)
and put it in every file that names the image, together. Within
PostgreSQL 18 a data folder opens on the newer server as it is. pgvector
asks for one more step after it is upgraded, `ALTER EXTENSION vector
UPDATE` in the database; the gallery runs it at start, after its
migrations, so in a normal install nobody types it. It needs a gallery
that writes (not the read-only build) and a database login that owns the
extension, which the login that created it does; when it cannot, the
gallery says why in its log and carries on. A move to PostgreSQL 19 is a
dump and restore and a release of its own.

The all-in-one's database differs from the compose install's where one
container must: it listens inside its container only and asks for the
password on network connections even there, its files are in the config
folder, and its locale
is spelled `en_US.UTF-8` where the image's is `en_US.utf8` (the same
locale).

The inference base and the CPU worker both download the models from
Hugging Face at the same pinned revisions and check the sha256 of every
weight file and of the tagger's tag list (configuration and tokenizer files come with the pinned revision
and are not separately checked). The weights have their own licences;
read them before you redistribute an image that contains them.

## What is pinned, and why it matters

The worker decides which frames are published and what their pixels are,
so a rebuild must not drift:

- **jellyfin-ffmpeg 8.1.3-1**, the exact package by sha256 (the project
  publishes none; the one in the Dockerfile was computed from the release
  asset). Tone-mapped output differs between ffmpeg versions, so a new
  release is tried before it is adopted: build with
  `--build-arg JELLYFIN_FFMPEG_MAJOR=`, `_VERSION=` and `_SHA256=`,
  extract the same files in both images and compare the frames. The 7.1
  line's HDR tone mapper pushed reds toward pure red (an in-place matrix
  bug, fixed in 8.1); SDR output is pixel-identical on both. It is GPL v3
  or later; its source is that tag of
  `github.com/jellyfin/jellyfin-ffmpeg`.
- **The Python packages** in `containers/worker/requirements.txt`, pinned
  to the set every existing bundle was made with. Scene detection and the
  near-duplicate signatures live in them.
- **The palette tool**, built from `providers/palette-descriptor` with its
  `Cargo.lock` on a pinned Rust image. The analyze worker builds check its
  sha256.
- **LF line endings.** The pipeline records the sha256 of
  `pipeline_cli.py` in every bundle; the worker build refuses a checkout
  with CRLF.

## What has been proven

On 2026-10-02 the whole chain was built from nothing (no build cache) on
one machine and each image compared with the one then in service:

- worker: the same episode extracted in it gave the same 62 published
  frames (sha256, timestamps and shots), audit records, descriptors and
  web images, byte for byte;
- inference base: every model file identical, the same package set;
- analyze workers: on the same real run records, the same 57 picks in
  the same order, every tag, face and label record identical, and every
  embedding bit-identical (GPU against GPU, CPU against CPU; a CPU
  embedding differs from a GPU one in the last bits, as always);
- text encoder: eight phrases, every vector bit-identical.

The gallery has only ever been built from its file. What this does not
show: a build on a machine with a different CPU or Docker version, and
anything after the pinned sources move (a base image tag withdrawn, a
model repository changed).

Until 2026-10-02 the worker in service was layered on an image delivered
in July; the build above replaced it after the check described.
