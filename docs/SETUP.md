# Setting up and keeping it running

The README has the five install steps. This page is what sits around
them: what you need before you start, how to tell the install works, and
what to do when something stops. Using the gallery (searching,
reviewing) is [USING.md](USING.md).

## What you need

- **Docker with Compose** on a 64-bit x86 machine. Linux, or Docker
  Desktop on Windows. Built and tested on Docker Desktop for Windows;
  a Linux host should work and has not been tested from this file.
- **Disk for the images: about 30 GB**, most of it the model base
  (18 GB). The CPU analyze worker is 6 GB on top of that base, the GPU
  one shares it.
- **Disk for the library.** For a 24-minute 1080p episode, measured:
  about 1 GB while it waits for your review (the picked frames plus the
  ones the picker passed over, as lossless PNG), dropping to roughly
  150 MB once you finish reviewing and the extras are deleted, plus
  about 13 MB of web images. A 4K film is several times that.
- **The folder your video is in**, on the same machine or mounted there.
  It is mounted read-only.
- **For the GPU analyze worker:** an NVIDIA card and the NVIDIA Container
  Toolkit (Docker Desktop on Windows has it built in with WSL 2). The
  CPU worker needs neither.
- **Internet for the build only.** The build pulls base images, Python
  and Node packages, FFmpeg and the model weights. Nothing is downloaded
  at run time and nothing is ever uploaded.
- **Optional: Shoko.** With it you pick series and episodes from a list.
  Without it you type a file's path and its title. Connecting it has
  its own page, [SHOKO.md](SHOKO.md); get one file through without it
  first.

How long a 24-minute episode takes, on the maintainer's machines: about
four minutes to find shots and pull frames, two and a half minutes to
describe and pick on an RTX 4070 (four on CPU), two and a half minutes
for the web images.

## A first run that proves it works

You need one video file. If you would rather not start with your own
library, the Blender open movies are free to download and work as a test.

1. Put the file in the folder `HIKARI_VIDEO` names.
2. Open the gallery, choose **Admin**, sign in, choose **Onboard**, then
   "Add a file by its path".
3. Pick the source folder (`library` unless you renamed it), type the
   file's path inside that folder, a title, an episode number, and
   submit.
4. Open **Jobs**. You should see four stages for the work: find the
   shots and pull frames, describe each frame and pick the stills, make
   the web images, add them to the gallery.

**It works when** all four finish and the stills appear under Library,
each opening to a page with a palette and labels. Typing a phrase into
Explore with "by mood" selected should rank the stills; that proves the
text encoder is up.

**It does not work when** a stage sits waiting "for the source worker
(not checked in)" or the same for the analyze worker: that container is
not running. See below.

## When something stops

Start with the Jobs page. It says what each stage is doing or why it is
not, in words, and it has the buttons that fix most things.

| What Jobs says | What it means | What to do |
|---|---|---|
| waiting for a worker "(not checked in)" | That worker's container is stopped or cannot reach the gallery. | `docker compose ps`, then `docker compose logs --tail 50 worker` (or `analyze-cpu` / `analyze-gpu`). Start it with `docker compose up -d`. The stage picks up by itself. |
| "to retry", with tries used | The stage failed in a way that may pass next time (a network blip, a restart). It waits 15 minutes, longer each time, and tries up to three times. | Nothing, or press **Retry now**. |
| "gave up" | Three tries failed. The reason is printed under the row. | Fix what the reason names, then **Retry**. |
| "refused" | The input can never work: an identity with a missing field, a video whose color tags the tool does not know, a path outside the source folder. | Read the reason. For a wrong path or title, **Remove…** the work and add it again. |
| "blocked, needs a human" | The file is not where the job says, or it changed since it was chosen. | Put the file back or fix the mount, then **Retry**. |
| a stage "running" with no progress for more than ten minutes | The worker died mid-stage. | Nothing: after ten minutes without a heartbeat the stage is handed out again. |

Other things that go wrong:

- **The database will not start** and its log says a superuser password
  is not specified: the first-run lines are not in `.env` yet.
- **Sign-in fails with the right password**, or the form does nothing:
  `HIKARI_ORIGIN` is not the address in your browser's address bar. Fix
  it in `.env` and `docker compose up -d`.
- **"No worker has reported a source folder yet"** on the Onboard page:
  the source worker has not started. It waits for the gallery to be
  healthy first, which takes about half a minute.
- **The worker cannot write** to a data folder you chose yourself
  (`HIKARI_DATA`, `HIKARI_IMAGES` as paths): the containers run as user
  id 10001, and the folder must be writable by it.
- **The worker cannot read your video** (Linux): mounting it read-only
  does not grant access. User id 10001 needs read permission on the
  files and permission to enter every folder above them. A library
  readable only by its owner or a media group needs an ACL or a wider
  mode for that user.
- **An analyze worker says it cannot read a bundle and to update
  whichever worker is behind:** the source worker and the analyze worker
  were built from different versions. Rebuild both.
- **You lost the admin password:** run the first-run step again and
  replace three lines in `.env` with the new ones: `HIKARI_ADMIN_USER`,
  `HIKARI_ADMIN_PASSWORD_HASH` and `HIKARI_SESSION_SECRET`. Keep the
  database password line as it is. Then `docker compose up -d gallery`
  so the gallery reads them.

Logs for any part: `docker compose logs --tail 100 <service>`, where the
services are `gallery`, `worker`, `analyze-cpu` or `analyze-gpu`,
`text-encoder` and `db`. Each work also keeps its own stage logs beside
its frames (`extract.log`, `derive.log` in its folder on the data
volume).

Normal operation needs no shell beyond these commands and no SQL. If you
find yourself editing the database by hand, that is a bug worth
reporting.

## Backups

Three things hold your library:

1. **The database** holds your review work: what you culled, corrected,
   pinned and hid. Nothing else can rebuild that. A copy of the
   database's folder taken while it runs is not a backup; a dump is.

2. **The data** (`hikari-data`) holds the original frames.
3. **The web images** (`hikari-images`) are what the gallery serves.

Back up all three together. The database alone restores records that
point at files which are not there, and once you finish reviewing a work
and its extra frames are deleted, the tool will not re-run it: the way
back from lost files is to remove the work and onboard it again, which
loses your review of it.

The easy way to back up the files is to keep them in folders of your own
rather than Docker volumes: set `HIKARI_DATA` and `HIKARI_IMAGES` in
`.env` to paths before the first start, and include those folders in
whatever already backs up the machine. If you leave them as Docker
volumes, they are named after the folder you cloned into (for example
`hikari-index_hikari-data`; `docker volume ls` shows them), and you back
them up the way you back up any Docker volume. Either way keep the
owner, user id 10001.

To take a matching set, stop everything that writes, leaving only the
database up. The gallery is one of the writers: it runs the last stage
itself, stores what analyze workers send back, and saves your review.

```bash
docker compose stop gallery worker analyze-cpu analyze-gpu
```

Dump the database to a file inside its container, then copy the file
out. (These two steps work in any shell; redirecting the dump with `>`
corrupts it in Windows PowerShell.)

```bash
docker compose exec db pg_dump -Fc -U hikari -f /tmp/hikari-backup.dump hikari
```

```bash
docker compose cp db:/tmp/hikari-backup.dump ./hikari-backup.dump
```

Copy the data and web image folders, then start everything again:

```bash
docker compose up -d
```

To restore on a fresh install: put the files back first, start only the
database, copy the dump in, load it, then start the rest.

```bash
docker compose up -d db
```

```bash
docker compose cp ./hikari-backup.dump db:/tmp/hikari-backup.dump
```

```bash
docker compose exec db pg_restore -U hikari -d hikari --no-owner --clean --if-exists /tmp/hikari-backup.dump
```

```bash
docker compose up -d
```

Your `.env` is part of the backup too: the database was created with the
password in it.

## Updating

```bash
git pull
```

```bash
docker compose build
```

```bash
docker compose up -d
```

The gallery applies any database changes itself when it starts. Runs
already on disk stay readable by newer versions. Rebuild `inference-base`
only when the release notes say the models changed.

## Stopping and removing

`docker compose down` stops everything and keeps your data.
`docker compose down -v` also deletes the Docker volumes: the database,
the frames and the web images, unless you pointed those at folders of
your own, which it leaves alone. Your video is never touched by either.
