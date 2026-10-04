# Working in this repository

Hikari Index turns a chosen episode or film into a small, searchable set
of representative stills with palette, composition, tag and similarity
data, and serves them from a gallery on your own network. This file is
for anyone (person or coding agent) changing the code. `README.md` says
what the tool is and how to run it; `docs/HOW_IT_WORKS.md` explains the
pipeline and the rules it keeps; `docs/SETUP.md` is requirements, recovery and backups; `docs/SHOKO.md`
connects Shoko; `docs/SECOND_MACHINE.md` puts an analyze worker on another
machine;
`docs/BUILDING.md` builds the images.

## Layout

| Path | What it is |
|---|---|
| `gallery/` | The web app (SvelteKit, Node server build): public pages, the admin area, the job table, the import stage, the worker API. `gallery/README.md` covers development. |
| `worker/hikari_worker/` | The workers' Python: the raw-source worker (extraction, web images, listings, deletions) and the analyze worker (models, picker). |
| `providers/frame_sample/` | Extraction: shot detection, frame choice, exact frame pull with the pinned FFmpeg. The frame, timestamp and colour reference. `bundle.py` writes and checks the folder of frames the other stages read. |
| `providers/annotation/`, `providers/inference/`, `providers/selection/` | Labels, embeddings and the still picker, run by the analyze worker. |
| `providers/palette-descriptor/` | The colour descriptor (Rust). |
| `providers/web_derivatives/` | The AVIF web images. |
| `containers/` | One Dockerfile per image: `worker`, `inference` (the analyze base), `rtx-worker`, `cpu-worker`, `text-encoder`. The gallery's is `gallery/Dockerfile`. |
| `tests/` | Synthetic-fixture tests for the providers. |
| `compose.yaml`, `.env.example` | The single-machine install: every service, built from source, one settings file. |
| `compose.rtx-worker.yaml`, `compose.cpu-worker.yaml`, `compose.db.yaml` | An analyze worker on a second machine (GPU or CPU), and a database for gallery development. Each has its `.env.*.example`. |

## Build, run, test

- Images: `docs/BUILDING.md`. The tag you pass is the version the image
  reports.
- Gallery development: `gallery/README.md` (`npm run dev`, `npm run build
  && node build`, schema changes through `drizzle-kit generate`).
- Python tests: `python -m pytest tests/annotation tests/inference
  tests/frame-sample tests/web-derivatives` from the repository root
  (Python 3.12), in an environment with `pytest`, `numpy`, `Pillow` built
  with AVIF support, and `providers/frame_sample/requirements.txt`. They
  use synthetic fixtures only; a test that needs a real file takes a path
  from the environment and skips without one. The gallery has no test
  suite yet; `npm run build` is its check.
- Verify a built image on a real input before you call it delivered.
  Tests and code review have both missed what the first real run caught.

## Rules the code keeps

These are not conventions; code that breaks one is wrong.

- **Shoko is read-only.** The gallery reads series, episodes and files
  from a fixed allowlist of GET paths and never calls anything that
  changes Shoko's state. A work added from a local file is described by
  the operator and never looked up in Shoko.
- **Nothing runs until a person confirms it.** Onboarding starts from an
  explicit choice on the Onboard page: one file, one episode, a season,
  or a listed folder. The library is never scanned as a whole.
- **Source media is read-only and never leaves the network.** No cloud
  processing of video, ever.
- **Raw video is opened only by the worker that mounts it.** Readiness,
  fingerprint, probe, shots, decode and extraction happen there. The
  analyze worker gets an immutable prepared bundle and no raw mount or
  catalogue credential. The gallery never opens source files.
- **Software FFmpeg is the frame, timestamp and colour reference.** Colour
  conversion follows the source's own colour tags; the 709 assumption is
  only the fallback for untagged SDR, and unknown combinations are
  refused. Never add a force-709 filter. A hardware decode path needs
  parity evidence before it touches the reference.
- **Web images are AVIF q85 4:4:4 with a declared sRGB intent.** Masters
  and web images are separate storage; no public address resolves a
  master.
- **Models run on reduced candidates, never on every frame.** They are
  versioned signal generators behind a fixed vocabulary with an abstain
  state. No named-character, demographic or identity inference.
- **Normal operation needs no shell and no hand-written SQL.** Schema
  changes are migrations the app applies; the admin pages do the work.
- **Out of scope:** named-character identification, public uploads,
  whole-library scanning, generated captions presented as fact.

## Conventions

- Small repository, mature dependencies, framework defaults over bespoke
  infrastructure. No Kubernetes, Kafka, separate search or vector
  service, or separate queue until a measurement says so. Check licence
  and maintenance before adding a production dependency; prefer one
  already present.
- Record tool, model and configuration provenance with every result. It
  is a note for whoever reads the run later; nothing may refuse a result
  because a build version differs. Refuse on a version only when the
  meaning changed (vectors from a different embedding model).
- Stages talk through files on disk. A reader takes the fields it uses
  and ignores the rest; a writer may add a field without asking anyone.
  Check what you consume (a missing or changed frame, a list that does
  not match its checksum, a path or id that would leave its folder) and
  refuse it in words that say what to do. When a field's meaning
  changes, handle that explicitly where it is read. A list of allowed
  fields or a format-version gate in a stage that does not read the
  field is what broke the analyze stage over a rename; do not add one
  for its own sake.
  `docs/HOW_IT_WORKS.md` has the three kinds of rule and two worked
  examples (add a bundle field, add a label value).
- Run one real work through every stage (extract, analyze, web images,
  import) before you call a change to any of them done.
- Comments and docs record decisions, invariants and non-obvious
  behaviour, not narration. Plain words, US spelling.
- Text files are LF (`.gitattributes`). The worker build refuses CRLF in
  the pipeline, whose digest is recorded in every bundle.
- Comments cite decision records as `ADR-NNNN` and the maintainer's
  design notes as `research/NN`. Both are kept outside this repository;
  the citation is the stable name of a decision, and the comment around
  it states what was decided. If a comment leans on one without saying
  why, that comment is the thing to fix.
- Firefox and Chrome are the browsers the gallery is tested in; keyboard
  use, a responsive layout and basic accessibility are part of done.

## What never goes in a commit

Source media, stills or anything derived from real frames; real titles,
ids, hashes, hostnames, addresses or local paths; credentials, `.env`
files, keys; model weights; databases, dumps, logs; anyone's private
deployment configuration. Tests use synthetic or redistributable fixtures.
