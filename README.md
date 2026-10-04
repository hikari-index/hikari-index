# Hikari Index

A local-first anime cinematography reference library. You pick an episode,
a film or a season from your own collection; Hikari Index pulls a few
dozen stills that stand for it, describes each one (palette, framing,
tags, an image vector for likeness and mood search), and serves them from
a gallery on your network. Your video is never changed, scanned as a
whole, or sent anywhere.

**Documentation: https://docs.hikari-index.moe/**:
what you need, installing, connecting Shoko, using the gallery, recovery,
backups and updating. Its source is
[hikari-index/hikari-index-docs](https://github.com/hikari-index/hikari-index-docs).

In this repository: [how it works](docs/HOW_IT_WORKS.md) (the pipeline
and the rules it keeps, for anyone changing the code),
[building](docs/BUILDING.md) the images by hand, and
[AGENTS.md](AGENTS.md) for contributors.

## What it needs

- A machine that has the video and can run containers (Docker with
  Compose, amd64): it runs the gallery, its PostgreSQL database and the
  worker that reads the files.
- Somewhere to run the models: a machine with an NVIDIA GPU is quick, a
  CPU-only machine works and is slow. It needs no access to your video.
- [Shoko](https://shokoanime.com/) is optional and recommended: the
  gallery reads your series, episodes and files from it (read-only, a
  non-admin key). Without it you add files by path or a folder at a time.

The gallery, the worker that reads your video, the CPU analyze worker and
the text encoder are published on the GitHub Container Registry
(`ghcr.io/hikari-index/...`), so an install pulls them; the
[install page](https://docs.hikari-index.moe/install/) walks through it.
The GPU analyze worker is built on your machine. Versions follow
[docs/RELEASING.md](docs/RELEASING.md).

## License

GNU Affero General Public License, version 3 or later (see `LICENSE`).
`NOTICE` lists the third-party work the built images contain or download.
