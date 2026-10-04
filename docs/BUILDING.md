# Building the images

Every image builds from this repository and public base images. Nothing
is pulled from a private registry. For an install on one machine,
`docker compose build` does all of this for you (see the README); the
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
