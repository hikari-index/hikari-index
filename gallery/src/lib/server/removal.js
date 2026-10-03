// Removing an episode, a season or a whole title from the index (maintainer
// decisions 2026-09-29; ADR-0009 amendment). Everything of each work goes:
// its rows here (the work, its stills with review marks and corrections, the
// job chains that made it) at once, and its files on disk (run folder with
// masters, surplus and records; web images) by a `scrub` job on the source
// worker, the only machine that can delete the images. A removal record
// stays behind, and the same file can be onboarded again at any time.
import { randomUUID } from "node:crypto";
import { shownEpisodeTitle } from "$lib/titles.js";
import { lstat, readdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { sql } from "drizzle-orm";
import { env } from "$env/dynamic/private";
import { db } from "$lib/server/db/index.js";
import { OPERATOR_LOCK } from "$lib/server/jobs.js";
import { WORK_ID } from "$lib/server/onboard.js";

// What a work's folders hold now, for the confirm page: the run folder on
// the array (masters, surplus, records) and its web images. Read-only; a
// folder that is not there counts as nothing. Null when a mount is missing.
async function folderUse(path) {
  let files = 0;
  let bytes = 0;
  try {
    for (const e of await readdir(path, { recursive: true, withFileTypes: true })) {
      if (!e.isFile()) continue;
      try {
        bytes += (await lstat(join(e.parentPath ?? e.path, e.name))).size;
        files += 1;
      } catch {
        // gone meanwhile
      }
    }
  } catch (e) {
    if (e.code === "ENOENT") return { files: 0, bytes: 0 };
    return null;
  }
  return { files, bytes };
}

export async function diskUse(ids) {
  const runs = resolve(env.HIKARI_RUNS || "/runs");
  const images = resolve(env.HIKARI_IMAGES || "/images");
  const out = {};
  for (const id of ids) {
    // Ids come from the database, but a path is built from them: only the
    // work id shape is ever joined to a mount.
    out[id] = WORK_ID.test(id) ? { run: await folderUse(join(runs, id)), images: await folderUse(join(images, id)) } : null;
  }
  return out;
}

export const SCOPES = new Set(["work", "season", "franchise"]);
// A season's or title's id: Shoko's, or below zero for a local one (ADR-0014).
const SHOKO_ID = /^-?[1-9]\d{0,8}$/;
// A stage a worker holds right now: its lease is still running. A stage
// whose worker died keeps its state until the lease runs out; after that
// nobody holds it and it must not block a removal.
const HELD = ["leased", "running", "cancel_requested"];
const heldNow = (j) => HELD.includes(j.state) && j.lease_expires_at && new Date(j.lease_expires_at) > new Date();
// A scrub that has not deleted its folders keeps the work id: onboarding
// the same id before they are gone would mix the old run with the new one.
// Only a committed scrub releases it (scrubs cannot be cancelled; one that
// failed is retried from Jobs).
const SCRUB_DONE = "committed";
// Lists go to Postgres as bound parameters (drizzle expands a JS array to
// ($1, $2, ...)), never as hand-built array text: a work may be called
// "null", which array text reads as SQL NULL.
const STAGE_ORDER = { extract: 0, analyze: 1, derive: 2, import: 3 };

// A season or title that is not in the Library's tables yet (nothing of it
// was ever imported) is named by its series, not by its first episode.
const labelFor = async (q, scope, target, works) =>
  (await scopeLabel(q, scope, target)) ??
  (scope !== "work" ? works.find((w) => w.identity.series_title)?.identity.series_title : null) ??
  works[0]?.label ??
  String(target);

function episodeLabel(title, entryType, episode, episodeTitle) {
  const ep = entryType === "episode" && episode != null ? `E${String(episode).padStart(2, "0")}` : null;
  return [title, ep, shownEpisodeTitle(entryType, episodeTitle)].filter(Boolean).join(" · ");
}

// Every work id a scope covers: works in the gallery, and works that were
// onboarded but never imported (a chain that failed or is still waiting),
// found through their chains' Shoko identity. An id belongs to one series:
// the imported work's, or else its newest chain's. So an id once used by a
// cancelled chain of this series and now owned by another series' work is
// not swept up with this one.
async function workIds(q, scope, target) {
  if (scope === "work") {
    const r = await q.execute(sql`select ${target}::text as id where exists (select 1 from works where id = ${target})
      or exists (select 1 from jobs where work_id = ${target} and stage <> 'scrub')`);
    return r.rows.map((x) => x.id);
  }
  if (!SHOKO_ID.test(String(target))) return [];
  const seasons = scope === "season"
    ? [Number(target)]
    : (await q.execute(sql`select id from seasons where franchise_id = ${Number(target)}`)).rows.map((x) => x.id);
  if (!seasons.length || seasons.some((s) => !Number.isInteger(s))) return [];
  const r = await q.execute(sql`with cand as (
      select id from works where shoko_series_id in ${seasons}
      union select distinct work_id from jobs where stage <> 'scrub'
        and (params -> 'identity' ->> 'series_id')::int in ${seasons}
    ), owner as (
      select c.id, coalesce(w.shoko_series_id, (select (j.params -> 'identity' ->> 'series_id')::int from jobs j
          where j.work_id = c.id and j.stage <> 'scrub' order by j.created_at desc, j.id desc limit 1)) as series
      from cand c left join works w on w.id = c.id
    )
    select id from owner where series in ${seasons} order by 1`);
  return r.rows.map((x) => x.id);
}

async function scopeLabel(q, scope, target) {
  if (scope === "season") return (await q.execute(sql`select name from seasons where id = ${Number(target)}`)).rows[0]?.name ?? null;
  if (scope === "franchise") return (await q.execute(sql`select name from franchises where id = ${Number(target)}`)).rows[0]?.name ?? null;
  return null; // a work's label comes from describe()
}

// What each work is and what removing it deletes. With lock, the works'
// job rows are locked as they are read, so no worker can lease one of them
// (the lease query skips locked rows) until the removal commits.
async function describe(q, ids, lock = false) {
  if (!ids.length) return [];
  const works = (await q.execute(sql`select w.*,
      (select count(*) from stills s where s.work_id = w.id)::int as stills,
      (select count(*) from stills s where s.work_id = w.id and s.review_state <> 'unreviewed')::int as reviewed,
      (select count(*) from stills s where s.work_id = w.id and (s.facets_human is not null or s.tags_human is not null))::int as corrected
    from works w where w.id in ${ids}`)).rows;
  const byId = new Map(works.map((w) => [w.id, w]));
  const jobs = (await q.execute(sql`select id, chain_id, stage, state, attempt, work_id, params, result, created_at, finished_at, lease_expires_at
    from jobs where work_id in ${ids} and stage <> 'scrub' order by created_at, id ${lock ? sql`for update` : sql``}`)).rows;
  return ids.map((id) => {
    const w = byId.get(id);
    const own = jobs.filter((j) => j.work_id === id);
    const extract = own.filter((j) => j.stage === "extract").at(-1);
    const identity = extract?.params?.identity ?? own[0]?.params?.identity ?? {};
    const chains = [...new Set(own.map((j) => j.chain_id))].map((chainId) => ({
      chain: chainId,
      stages: own.filter((j) => j.chain_id === chainId).sort((a, b) => (STAGE_ORDER[a.stage] ?? 9) - (STAGE_ORDER[b.stage] ?? 9)).map((j) => ({
        stage: j.stage, state: j.state, attempt: j.attempt,
        created: j.created_at, finished: j.finished_at,
      })),
    }));
    return {
      id,
      label: w
        ? episodeLabel(w.title, w.entry_type, w.episode, w.episode_title_human ?? w.episode_title)
        : episodeLabel(identity.series_title, identity.entry_type, identity.episode, identity.episode_title) || id,
      imported: Boolean(w),
      stills: w?.stills ?? 0,
      reviewed: w?.reviewed ?? 0,
      corrected: w?.corrected ?? 0,
      held: own.some(heldNow),
      shokoSeriesId: w?.shoko_series_id ?? identity.series_id ?? null,
      shokoFileId: w?.shoko_file_id ?? identity.file_id ?? null,
      // A local file's hash and size are what its extraction found.
      fingerprint: extract?.params?.source?.fingerprint ?? extract?.result?.source?.fingerprint ?? null,
      size: extract?.params?.source?.size ?? extract?.result?.source?.size ?? null,
      bundleId: w?.bundle_id ?? null,
      identity: { series_title: w?.title ?? identity.series_title ?? null, entry_type: w?.entry_type ?? identity.entry_type ?? null,
        episode: w?.episode ?? identity.episode ?? null, episode_title: w?.episode_title_human ?? w?.episode_title ?? identity.episode_title ?? null },
      chains,
    };
  });
}

// For the confirm page. Null when the scope names nothing.
export async function removalPreview(scope, target) {
  if (!SCOPES.has(scope) || !target) return null;
  const q = db();
  const ids = await workIds(q, scope, target);
  if (!ids.length) return null;
  const works = await describe(q, ids);
  const disk = await diskUse(ids);
  for (const w of works) w.disk = disk[w.id];
  return { scope, target, label: await labelFor(q, scope, target, works), works, expect: [...ids].sort().join(",") };
}

// The database half, all or nothing. Refuses while a worker holds a stage
// of any of these works (cancel it on Jobs first): deleting rows under a
// running stage would leave it writing files for a work that no longer
// exists. `expect` is the sorted, comma-joined work ids the confirm page
// showed: the removal deletes exactly those or nothing, never a set that
// grew or changed while the page was open. Returns { id, works, stills } or
// { refused }.
export async function removeScope({ scope, target, expect, reason, requestedBy }) {
  if (!SCOPES.has(scope) || !target) return { refused: "Nothing to remove." };
  return db().transaction(async (tx) => {
    // One operator action at a time (jobs.js OPERATOR_LOCK): a repeated
    // submit finds its works already gone and is refused; a re-run or
    // retry cannot slip rows in while this decides.
    await tx.execute(OPERATOR_LOCK);
    const ids = await workIds(tx, scope, target);
    if (!ids.length) return { refused: "That is not in the index (any more). Nothing was removed." };
    if ([...ids].sort().join(",") !== String(expect ?? "")) {
      return { refused: `What this would remove changed since the page was shown (it now covers ${ids.length} ${ids.length === 1 ? "work" : "works"}). Nothing was removed; look at the list again.` };
    }
    const works = await describe(tx, ids, true);
    const held = works.filter((w) => w.held);
    if (held.length) {
      return { refused: `A worker is running a stage of ${held.map((w) => w.label).join(", ")}. Cancel it on Jobs, wait for it to stop, then remove.` };
    }
    const label = await labelFor(tx, scope, target, works);
    const id = randomUUID();
    await tx.execute(sql`insert into removals (id, scope, target, label, reason, works, requested_by)
      values (${id}, ${scope}, ${String(target)}, ${label}, ${reason || null}, ${JSON.stringify(works)}::jsonb, ${requestedBy})`);
    await tx.execute(sql`delete from works where id in ${ids}`); // stills go with them
    await tx.execute(sql`delete from jobs where work_id in ${ids} and stage <> 'scrub'`);
    for (const w of works) {
      const chainId = randomUUID();
      await tx.execute(sql`insert into jobs (id, chain_id, stage, capability, idempotency_key, work_id, params, requested_by)
        values (${chainId}, ${chainId}, 'scrub', 'source', ${`scrub:${id}:${w.id}`}, ${w.id},
          ${JSON.stringify({ removal: { id, scope, label }, identity: w.identity })}::jsonb, ${requestedBy})`);
    }
    return { id, label, works: works.length, stills: works.reduce((n, w) => n + w.stills, 0) };
  });
}

// Past removals, newest first, with how far each work's disk scrub got.
export async function listRemovals(limit = 50) {
  const r = await db().execute(sql`select r.*, coalesce((select json_agg(json_build_object(
        'work', j.work_id, 'state', j.state, 'bytes', (j.result ->> 'bytes')::bigint, 'error', j.error_message)
        order by j.work_id)
      from jobs j where j.stage = 'scrub' and j.params -> 'removal' ->> 'id' = r.id::text), '[]'::json) as scrubs
    from removals r order by r.created_at desc limit ${limit}`);
  return r.rows.map((x) => ({
    id: x.id, scope: x.scope, label: x.label, reason: x.reason, createdAt: x.created_at, requestedBy: x.requested_by,
    works: (x.works ?? []).length,
    stills: (x.works ?? []).reduce((n, w) => n + (w.stills ?? 0), 0),
    scrubs: x.scrubs,
  }));
}

// True while a removal of this work id has not finished deleting its
// folders (see SCRUB_DONE).
export async function beingRemoved(workId, q = db()) {
  const r = await q.execute(sql`select 1 from jobs where work_id = ${workId} and stage = 'scrub'
    and state <> ${SCRUB_DONE} limit 1`);
  return r.rows.length > 0;
}
