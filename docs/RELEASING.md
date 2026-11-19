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

### Is my pull request `action-needed`?

Ask one question: **after updating the way the docs say (pull the new
images, restart), does anything still not work, or not work the same,
until the person running it does something by hand?** If yes, label the
pull request `action-needed` and say in its description exactly what
they must do. If no, leave it unlabeled.

| Change | `action-needed`? |
|---|---|
| A setting renamed, removed, or newly required | yes |
| An edit needed to `compose.yaml`, an `.env` file or a container template | yes |
| Something to rebuild by hand (the GPU worker's base, a second machine's worker) | yes |
| Old and new images can't run side by side (a worker elsewhere must update at the same time) | yes |
| Work has to be onboarded again to keep working | yes |
| A feature or a setting removed | yes |
| A bug fix, a label rule or allowlist change, a new model version recorded with each run | no |
| A database migration the gallery applies by itself on start | no (say in the description if it stops a rollback) |
| A new optional setting with a default that keeps today's behavior | no |
| Better results only for work analyzed from now on, or after an optional re-run | no (say so in the description; re-running stays the user's choice) |
| Docs, tests, CI, comments | no (and not released at all) |

An `action-needed` pull request makes the next release MAJOR. When in
doubt, ask in the pull request rather than guessing.

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
| all-in-one: gallery, database, worker that reads your video and text encoder in one container | `ghcr.io/hikari-index/hikari-index` |

The gallery, the read-only gallery and the text encoder are published for
`linux/amd64` and `linux/arm64`; the two workers and the all-in-one image
for `linux/amd64` only.
The GPU analyze worker is not published (its base image is too large to
build on GitHub's runners); build it yourself, see
[BUILDING.md](BUILDING.md).

| Tag | Moves | Made when |
|---|---|---|
| `latest` | yes | a release |
| `1.5.1` (the version) | never | a release |
| `dev` | yes | a merge to `main`, except one that touches only docs and templates (below) |
| `sha-<short commit>` | never | the same merges |
| `build-<run id>` | not yours to pull | a release run, before it moves `latest` and the version (below) |

A version tag and a `sha-` tag are never moved, overwritten or deleted
once pushed. `latest` and `dev` are meant to move. A `build-` tag is the
release workflow's own working name for an image; the version tag ends
up on the same image.

A merge that touches only `docs/`, the markdown at the repository root
or the unRAID templates builds nothing: `dev` stays where it was and no
`sha-` tag is made for that commit. Any other merge builds every image,
changed or not, because each carries the commit's version. A release
always builds.

## Making a release

1. Decide the number from the definitions above, by looking at what was
   merged since the last release (`git log v1.5.0..main`). Any merged
   pull request labeled `action-needed` (see the table above) makes it
   MAJOR.
2. Tag `main` and push the tag:

   ```bash
   git tag v1.6.0
   git push origin v1.6.0
   ```

3. The images workflow builds every image from that commit. Only when
   all of them exist does it point the version and `latest` at them, so
   a build that fails moves neither for any image. Then it opens a draft
   release
   with the merged pull requests listed (`.github/release.yml` puts
   `action-needed` ones in their own section).
4. Edit the draft: the first line is **Action needed:** with what to do,
   or "None". Then two or three plain lines on what changed. Publish.

The Releases page lists releases only; every build is on the packages'
tag lists.

### When a release run fails

- **A build failed.** No image's version or `latest` has moved. On the
  run's page choose **Re-run failed jobs**.
- **The `publish` job stopped part-way.** Some images have the
  new tags and some do not. **Re-run failed jobs** finishes it with the
  images that run already built.
- **Re-run all jobs builds everything again**, replacing the images the
  run built before. That is fine while nothing of the version is out.
  Once any image has the version tag, the run stops at `publish` and
  says so: a version tag is never pointed at a second build. That
  version cannot be finished then; release the next patch. So after a
  `publish` that stopped part-way, use **Re-run failed jobs**, not this.

`latest` moves only when the version being released is the newest
version tag in the repository. Releasing `1.5.2` after `1.6.0` is out
publishes `1.5.2` and leaves `latest` on `1.6.0`. A newer tag counts
even if its own release failed: finish that release and `latest` moves
to it.

Push one release tag at a time. Release runs wait for each other, and
GitHub keeps only one waiting: a third cancels the second, which then
has to be run again.
