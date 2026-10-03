# gallery

The Hikari Index gallery: SvelteKit built as a plain Node server, with
PostgreSQL (pgvector) behind it. This file covers development. Running
it for real: the repository README and `compose.yaml`.

The gallery is a curated subset of the media library, not a mirror of it.
Shoko provides grouping and names (franchise → season → episode); only
seasons and episodes with stills appear, and no library inventory is shown.

What exists: a library grouped by franchise, the episode contact sheet (chronological, with
timeline position and key labels), a still page that walks to its
neighbours, and the explore page with facet filters. The contact sheet's
review view culls, keeps, locks and excludes stills; ordinary browsing
hides what review culled.

## Admin

Everything under `/admin` needs a sign-in; the public routes carry no
form actions. The header's Admin link is only a link (`HIKARI_ADMIN_LINK`
in `.env.example`). `/admin` lists episodes with unreviewed,
culled, hidden and corrected counts; `/admin/works/<id>` is the workbench
(cull, keep, restore, lock, hide, per still); `/admin/stills/<work>/<candidate>` edits
a still's labels and tags and its review state. Corrections are stored
beside the machine's values and survive re-imports.

One account today, from environment (see `.env.example`). Create or
reset it with:

```bash
node scripts/admin-password.js [name]   # writes .env and admin-password.txt; read, then delete the file
```

## Settings

`.env` (git-ignored; see `.env.example`):

- `DATABASE_URL`: the PostgreSQL connection string.
- `HIKARI_IMAGES`: the web images root the media route streams from
  (`<work>/<candidate>/w<width>.avif`).
- `HIKARI_RUNS`: the run folders (turns on the gallery's import stage).
- `HIKARI_ADMIN_USER`, `HIKARI_ADMIN_PASSWORD_HASH`, `HIKARI_SESSION_SECRET`:
  the admin account (made by `scripts/admin-password.js`).
- `HIKARI_SHOKO_BASE_URL`, `HIKARI_SHOKO_API_KEY_FILE`: read-only Shoko.
- `HIKARI_WORKER_TOKENS`: `name:token` per analyze worker.
- `ENCODER_URL`: the text encoder, for mood search.
- `HIKARI_GALLERY_VERSION`: baked into the image at build.

In production the built server does not load `.env`; pass the values as
environment.

## Commands

```bash
# database (from the repository root; credentials in .env.db)
docker compose --env-file .env.db -f compose.db.yaml up -d

# works come in through Onboard (/admin/onboard) and the job table, never by hand

# develop / build / run the built server
npm run dev -- --host
npm run build && node build
```

## Schema changes

Edit `src/lib/server/db/schema.js`, then:

```bash
npx drizzle-kit generate
```

Commit the file it writes under `migrations/`. The app applies pending
migrations when it starts (with the import stage on) or on its first
request; nothing else touches the schema.
