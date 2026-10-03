# Connecting Shoko

With [Shoko](https://shokoanime.com/) connected, the Onboard page lets you
search your series and pick episodes from a list, and the Library groups
works the way Shoko does. Hikari Index only ever reads from Shoko. It
uses nine fixed read addresses and refuses a key that belongs to an
admin user.

You need three things: a key for a non-admin Shoko user, Shoko's address,
and a mapping from Shoko's folders to where the worker sees the same
files.

## 1. A non-admin user and its key

1. In Shoko's web interface (Settings, the user list), create a user for
   this tool, for example `hikari-index`, give it a password, and leave
   "Administrator" off. An admin key is refused on purpose.
2. Get an API key for that user through Shoko's API. The web interface
   also has an API Keys page, but it makes keys for whoever is signed
   in, and only admin users can sign in there, so it cannot make this
   one. From any machine that can reach Shoko (the address is
   an example; 8111 is Shoko's usual port):

   ```bash
   curl -s -H "Content-Type: application/json" -d '{"user":"hikari-index","pass":"THE-PASSWORD","device":"hikari-index"}' http://192.0.2.20:8111/api/auth
   ```

   In Windows PowerShell, where `curl` is a different command:

   ```powershell
   Invoke-RestMethod -Method Post -Uri http://192.0.2.20:8111/api/auth -ContentType 'application/json' -Body (@{ user = 'hikari-index'; pass = 'THE-PASSWORD'; device = 'hikari-index' } | ConvertTo-Json)
   ```

   The answer is `{"apikey":"..."}` (PowerShell shows it as a small
   table with one `apikey` row).
3. Put the key, alone, in a file named `shoko-api-key` inside a folder
   named `secrets` beside `compose.yaml` (or wherever `HIKARI_SECRETS`
   points). The gallery runs as user id 10001 and must be able to read
   the file.

## 2. Tell the gallery where Shoko is

In `.env`:

```
HIKARI_SHOKO_BASE_URL=http://192.0.2.20:8111
```

Use an address the gallery container can reach: the server's address on
your network, not `localhost`. Then:

```bash
docker compose up -d gallery
```

**Check:** open Admin, then Onboard. With Shoko connected the page has a
series search and typing a title lists your series. If it says Shoko is
not configured, the address is missing. If it says the key file cannot
be read, check the file's name, folder and permissions. If it says the
key belongs to an admin user, make the key for the non-admin user.

## 3. Map Shoko's folders to the worker

Shoko knows each file as "managed folder N, then a path inside it" (older
versions call these import folders). The worker needs to know where
folder N is mounted in its container. That is `HIKARI_SOURCE_ROOTS`.

**One folder.** Say Shoko's managed folder 4 is `/mnt/media/anime` on
the machine running Hikari Index:

```
HIKARI_VIDEO=/mnt/media/anime
HIKARI_SOURCE_ROOTS=4=/source
```

`HIKARI_VIDEO` is always mounted at `/source` in the worker, so this
says "folder 4 is /source".

**Several folders under one parent.** Folder 4 is `/mnt/media/anime` and
folder 7 is `/mnt/media/films`:

```
HIKARI_VIDEO=/mnt/media
HIKARI_SOURCE_ROOTS=4=/source/anime,7=/source/films
```

You can add a name for files outside Shoko to the same list, for example
`4=/source/anime,7=/source/films,other=/source/other`.

**Folders with no common parent** need a second mount. Say folder 4 is
`/mnt/media/anime` and folder 7 is `/mnt/other-disk/films`. Keep
`HIKARI_VIDEO=/mnt/media/anime`, and create a file named
`compose.override.yaml` beside `compose.yaml` (Compose reads it by
itself) with the second folder, read-only:

```yaml
services:
  worker:
    volumes:
      - /mnt/other-disk/films:/source-films:ro
```

Then in `.env`:

```
HIKARI_SOURCE_ROOTS=4=/source,7=/source-films
```

What matters is the path below the managed folder. Shoko may see the
folder under a different name than this machine does (a share, a
container path); only the part inside the folder has to match, and it
will, because it is the same folder.

**Finding the number.** Shoko's API lists the folders with their ids:

```bash
curl -s -H "apikey: THE-KEY" http://192.0.2.20:8111/api/v3/ManagedFolder
```

In PowerShell:

```powershell
Invoke-RestMethod -Uri http://192.0.2.20:8111/api/v3/ManagedFolder -Headers @{ apikey = 'THE-KEY' }
```

On older Shoko versions the address ends in `/api/v3/ImportFolder`. The
list has every folder Shoko manages, the ones it imports from and the
ones it moves files to; map each one that holds files you want to
onboard. If neither address answers, onboard one episode anyway: the Jobs page will stop at
the first stage with "managed folder 4 is not mapped on this worker",
which tells you the number. Fix `.env`, restart the worker and press
Retry.

After changing `HIKARI_VIDEO` or `HIKARI_SOURCE_ROOTS`:

```bash
docker compose up -d worker
```

**Check:** onboard one episode from the search. The first stage should
start within a minute. "Blocked, needs a human" with a message about a
managed folder means the mapping is wrong; "is not at its Shoko location" means the worker looked where the mapping
sent it and found no file: check what is in the mounted folder, and the
path Shoko reports for the file (it may have moved since Shoko last saw
it).

## What Shoko is asked for

Only reads: who the key belongs to, series search, a series and its
episodes, an episode and its files, a file, and the group a series is
in. The list is in `gallery/src/lib/server/shoko-client.js`; a request
for anything else is refused before it is sent. The key is sent in the
`apikey` header and is never logged.

The size and a hash Shoko recorded for the file are checked against the
file on disk before any frame is pulled, so a file that changed since Shoko
hashed it is caught rather than quietly used.
