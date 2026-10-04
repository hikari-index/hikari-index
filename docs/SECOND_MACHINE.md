# An analyze worker on a second machine

The usual reason: the machine with your video has no GPU, and another
machine on your network does. The analyze worker is the only part that
can move. It never sees your video. It needs two things from the main
machine: the gallery's address, and read-only access to the data folder
where the extracted frames are.

Get the single-machine install working first (README), with at least one
episode through all four stages. Then:

## On the main machine

**1. Keep the data in a folder, and share it.** A Docker volume cannot be
shared over the network, so the data must live in a folder of yours. In
`.env`:

```
HIKARI_DATA=/path/to/hikari/data
```

The folder must be writable by user id 10001. If you already have works
in the default Docker volume, the simple way over is to remove them,
switch, and onboard them again.

Share that folder on your network, read-only, by whatever your system
uses (SMB or NFS). The share's top level must be the data folder itself,
so that `runs` is directly inside it. A separate account that can only
read this one share is worth the minute it takes.

**2. Give the new worker a name and a token.** Each worker has its own.
Make a token on the main machine:

```bash
docker compose exec gallery node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

Add it to `HIKARI_WORKER_TOKENS` in `.env`, after a comma, as
`name:token`. With a worker named `gpu`:

```
HIKARI_WORKER_TOKENS=analyze:<the token already there>,gpu:<the new token>
```

**3. Decide what the main machine's own analyze worker does.** In `.env`:

- Keep it as a fallback for when the other machine is off: leave
  `COMPOSE_PROFILES=cpu` and add `HIKARI_ANALYZE_STANDBY=1`. A standby
  worker takes a stage only when no regular analyze worker has checked
  in for ten minutes.
- Or turn it off: `COMPOSE_PROFILES=` (empty). Analyze then waits
  whenever the other machine is off.

Without one of these, both workers take stages as they come, and half
your episodes are described on the slow machine.

**4. Apply:**

```bash
docker compose up -d
```

## On the second machine

**5. Get the same code and build the worker.** The two machines must run
the same version, so clone the same commit.

First the model base, which both kinds of worker are built from (large,
once):

```bash
docker build -f containers/inference/Dockerfile -t hikari-index/inference:inference-d1f2552-20261002.1 .
```

Then the worker (after step 6, which names it). For a GPU:

```bash
docker compose --env-file .env.rtx-worker -f compose.rtx-worker.yaml build
```

For CPU only, use `.env.cpu-worker` and `compose.cpu-worker.yaml` in
every command from here on.

**6. Settings.** Copy `.env.rtx-worker.example` to `.env.rtx-worker`
(or the `cpu` pair) and set:

- `HIKARI_GALLERY_URL`: the main machine's address and port, for example
  `http://192.0.2.10:5183`. Not `localhost`.
- `HIKARI_WORKER_ID` and `HIKARI_WORKER_TOKEN`: the name and token from
  step 2.
- `HIKARI_WORKER_VERSION`: any label, for example `rtx-worker-local`. Set
  it before the build in step 5; it is what the worker reports on Jobs.

**7. Mount the share as a Docker volume.** The worker reads the data
through a Docker volume named `hikari-index-share`. Over SMB, with the
share at `//192.0.2.10/hikari-data`:

```bash
docker volume create --driver local --opt type=cifs --opt device=//192.0.2.10/hikari-data --opt o=addr=192.0.2.10,vers=3.0,ro,uid=10001,gid=10001,file_mode=0440,dir_mode=0550,nosuid,nodev,noexec,username=READER,password=PASSWORD hikari-index-share
```

Over NFS, with the export at `/export/hikari-data`:

```bash
docker volume create --driver local --opt type=nfs --opt o=addr=192.0.2.10,ro,nfsvers=4 --opt device=:/export/hikari-data hikari-index-share
```

Docker keeps the share password in the volume's settings on this
machine, which is one more reason for a read-only account. This works
the same on Linux and on Docker Desktop for Windows; a drive letter
mapped in Windows cannot be used instead. The SMB form is the one the
maintainer runs; the NFS form is the standard Docker one and has not
been tested here.

**8. Start it:**

```bash
docker compose --env-file .env.rtx-worker -f compose.rtx-worker.yaml up -d
```

Always pass `--env-file`: without it Compose reads `.env`, which is the
main install's file.

## Check

On the main machine's Jobs page the new worker appears by its name with
its version, checked in. Onboard an episode: its second stage should be
taken by the new worker.

- **The worker never appears:** `docker compose --env-file .env.rtx-worker -f compose.rtx-worker.yaml logs --tail 20`.
  "gallery not reachable" is the address or a firewall. A refused token
  means the name and token in step 6 do not match the pair in step 2, or
  the gallery was not restarted after step 2.
- **It appears but the stage fails with "the run root is not readable
  ... (is the share mounted?)":** the volume does not show the data
  folder. Check that `runs` is at the top of the share, and the share
  account can read it.
- **It says it cannot read a bundle and to update whichever worker is
  behind:** the two machines were built from different commits.
- **Both workers take stages:** step 3 was skipped.
