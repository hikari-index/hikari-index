# Versions, images and releases

## Version numbers

Hikari Index uses [Semantic Versioning](https://semver.org/) from `1.0.0`.
The three numbers are defined by what the person running it has to do
when they update:

- **MAJOR** (`2.0.0`): you have to act. A setting to change, an edit to
  the compose file or a container template, something to rebuild by
  hand, work to onboard again. Also any update after which the new and
  old images cannot run side by side, for example an analyze worker on
  another machine that must be updated at the same time. Removing a
  setting or a feature is major, however rarely it was used.
- **MINOR** (`1.5.0`): something new or visibly different, and nothing
  for you to do. A database migration that applies itself on start
  counts here; if it means you cannot roll back to the previous version,
  the release notes say so.
- **PATCH** (`1.5.1`): fixes only. Nothing new, nothing to do. A
  security fix is a patch whose notes say to update soon.

A change to the docs, tests or CI alone is not released.

Every image carries the same version. A release builds all of them from
one commit, even if only one changed, so "run the same version
everywhere" means "every image shows the same number".

## Images and tags

One package per image on the GitHub Container Registry:

| Image | Package |
|---|---|
| gallery | `ghcr.io/hikari-index/gallery` |
| gallery, read-only (a viewer with no admin area) | `ghcr.io/hikari-index/gallery-readonly` |
| worker that reads your video | `ghcr.io/hikari-index/worker` |
| analyze worker, CPU | `ghcr.io/hikari-index/cpu-worker` |
| text encoder | `ghcr.io/hikari-index/text-encoder` |

The GPU analyze worker is not published (its base image is too large to
build on GitHub's runners); build it yourself, see
[BUILDING.md](BUILDING.md).

| Tag | Moves | Made when |
|---|---|---|
| `latest` | yes | a release |
| `1.5.1` (the version) | never | a release |
| `dev` | yes | every merge to `main` |
| `sha-<short commit>` | never | every merge to `main` |

A version tag and a `sha-` tag are never moved, overwritten or deleted
once pushed. `latest` and `dev` are meant to move.

## Making a release

1. Decide the number from the definitions above, by looking at what was
   merged since the last release (`git log v1.5.0..main`). A pull
   request that needs the user to act carries the `action-needed` label.
2. Tag `main` and push the tag:

   ```bash
   git tag v1.6.0
   git push origin v1.6.0
   ```

3. The images workflow builds every image from that commit, pushes the
   version and `latest`, and opens a draft release with the merged pull
   requests listed (`.github/release.yml` puts `action-needed` ones in
   their own section).
4. Edit the draft: the first line is **Action needed:** with what to do,
   or "None". Then two or three plain lines on what changed. Publish.

The Releases page lists releases only; every build is on the packages'
tag lists.
