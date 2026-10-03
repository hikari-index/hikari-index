# Hikari Index

A local-first anime cinematography reference library. You pick an episode,
a film or a season from your own collection; Hikari Index pulls a few
dozen stills that stand for it, describes each one (palette, framing,
tags, an image vector for likeness and mood search), and serves them from
a gallery on your network. Your video is never changed, scanned as a
whole, or sent anywhere.

Read [how it works](docs/HOW_IT_WORKS.md) first. [Setting up](docs/SETUP.md)
covers what you need, a first run that proves the install works, what to
do when something stops, backups and updating. [Building](docs/BUILDING.md)
makes the images by hand; [AGENTS.md](AGENTS.md) is for
anyone changing the code.

## What it needs

- A machine that has the video and can run containers: it runs the
  gallery, its PostgreSQL database and the worker that reads the files.
- Somewhere to run the models: a machine with an NVIDIA GPU is quick, a
  CPU-only machine works and is slow. It needs no access to your video.
- [Shoko](https://shokoanime.com/) is strongly recommended: the gallery
  reads your series, episodes and files from it (read-only, a non-admin
  key) and groups the library the way Shoko does. Without it you add
  files by path or a folder at a time and type the names yourself.

## Install

One machine, everything built from this repository. You need Docker with
Compose, about 30 GB of disk for the images, and the folder your video
is in.

1. Copy `.env.example` to `.env` and set three things in it: the folder
   your video is in, the address you will open the gallery at, and which
   analyze worker to run (`cpu` or `gpu`).
2. Build. The first command downloads the models and takes a while.

   ```bash
   docker compose build inference-base
   ```

   ```bash
   docker compose build
   ```

3. Make the secrets. This prints six lines to paste at the end of `.env`,
   and your admin password, once.

   ```bash
   docker compose run --rm --no-deps gallery node scripts/first-run.js
   ```

4. Start it.

   ```bash
   docker compose up -d
   ```

5. Open the gallery (`http://localhost:5183` unless you changed it),
   choose **Admin**, sign in, and go to **Onboard**. Without Shoko, add a
   file by its path inside your video folder. Jobs shows the four stages
   as they run; when the last one finishes the stills are in Library.

Connecting Shoko: [docs/SHOKO.md](docs/SHOKO.md).

Advanced, and not yet written up as steps: an analyze worker on a second
machine (a GPU elsewhere on your network). `compose.rtx-worker.yaml` and
`compose.cpu-worker.yaml` are how the maintainer runs it; they expect
the data folder shared over the network as a Docker volume on that
machine, the image built there, and a token of its own added to
`HIKARI_WORKER_TOKENS`. Start with everything on one machine.

This is the maintainer's own working install, being prepared for others.
Images are not yet published to a registry, which is why you build them.

## License

GNU Affero General Public License, version 3 or later (see `LICENSE`).
`NOTICE` lists the third-party work the built images contain or download.
