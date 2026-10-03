// Adding a work from a local file, without Shoko (ADR-0014). The operator
// names the file by a source folder the worker has mounted and a path
// inside it, and types what it is. The gallery never opens the file and
// never lists the library itself: the worker that runs the extraction
// resolves the path, refuses anything outside the folder or with more than
// one video stream, and takes the file's hash there. So nothing about the
// file (its size, its length, whether it exists) is known when it is queued.
//
// A whole folder at once (the 2026-10-03 amendment) goes the same way: the
// gallery queues a `list` job naming the folder, the worker answers with
// the video files' names and sizes, and the operator confirms which of them
// become works. Nothing runs before that confirm.
import { randomUUID } from "node:crypto";
import { sql } from "drizzle-orm";
import { db } from "./db/index.js";
import { insertChain, OPERATOR_LOCK, POLICY, workIdTaken } from "./jobs.js";
import { beingRemoved } from "./removal.js";

// A source folder's name: a key of the worker's HIKARI_SOURCE_ROOTS.
export const ROOT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

// The path as the job carries it: forward slashes, no leading slash, no
// empty, "." or ".." part. Null when nothing usable is left. The worker
// checks containment again on the real filesystem; this only keeps
// nonsense out of the job table.
export function cleanPath(value) {
  const text = String(value ?? "").trim().replaceAll("\\", "/");
  if (!text || text.length > 1000 || /[\u0000-\u001f\u007f]/.test(text)) return null;
  const parts = text.split("/").filter(Boolean);
  if (!parts.length || parts.some((p) => p === "." || p === "..")) return null;
  return parts.join("/");
}

// One line of typed text: control characters out, runs of spaces to one.
export function cleanLine(value, max = 200) {
  const text = String(value ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  return text.length > max ? null : text;
}

// The first stage's key for a local file. A Shoko file is keyed by its
// hash and size; nobody has hashed this one yet, so it is keyed by where it
// is: the same folder and path queue once.
export function localKey(root, path) {
  return `extract:local:${root}:${path}:${POLICY.extraction}:${POLICY.budget}`;
}

// The source folders the workers that open raw source have reported, by
// name. `reported` is false when no such worker has reported any (one from
// before this page reports nothing and cannot run a local file).
export async function sourceRoots() {
  const r = await db().execute(sql`select source_roots from workers where capabilities ? 'source'`);
  const names = new Set();
  for (const w of r.rows) {
    for (const n of Array.isArray(w.source_roots) ? w.source_roots : []) if (ROOT_NAME.test(String(n))) names.add(String(n));
  }
  return { names: [...names].sort((a, b) => a.localeCompare(b, "en", { numeric: true })), reported: r.rows.some((w) => Array.isArray(w.source_roots)) };
}

// The local series and titles there are, for the form's suggestions.
export async function localCatalogue() {
  const series = await db().execute(sql`select s.name, f.name as title from seasons s join franchises f on f.id = s.franchise_id
    where s.id < 0 order by lower(s.name)`);
  const titles = await db().execute(sql`select name from franchises where id < 0 order by lower(name)`);
  return { series: series.rows, titles: titles.rows.map((t) => t.name) };
}

// The live chain a folder and path already have, if any.
export async function existingLocal(root, path, q = db()) {
  const r = await q.execute(sql`select chain_id, work_id, state from jobs
    where idempotency_key = ${localKey(root, path)} and state not in ('cancelled', 'dead_letter')`);
  return r.rows[0] ?? null;
}

// The same for many paths in one folder: path -> live chain.
export async function existingLocalMany(root, paths, q = db()) {
  if (!paths.length) return new Map();
  const keys = paths.map((p) => localKey(root, p));
  const r = await q.execute(sql`select idempotency_key, chain_id, work_id, state from jobs
    where idempotency_key in ${keys} and state not in ('cancelled', 'dead_letter')`);
  const byKey = new Map(r.rows.map((row) => [row.idempotency_key, row]));
  return new Map(paths.map((p) => [p, byKey.get(localKey(root, p)) ?? null]).filter(([, v]) => v));
}

// ---- Folder listings: a `list` job the worker answers (ADR-0014
// amendment). The row lives in the jobs table like any stage (the worker's
// lease, lease expiry and attempts all apply), under a work id that is not
// a work; the Jobs page leaves these rows out (jobs.js LISTING_STAGE).

export const LISTING_WORK = "folder-listing";
// A listing nobody has answered within this is given up on, and answered
// ones are kept this long for the page's "recent" list.
const LISTING_STALE_HOURS = 2;
const LISTING_KEEP_DAYS = 7;

export const LISTING_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// Queues one listing and returns its id (the page the operator waits on).
// Listings nobody answered within LISTING_STALE_HOURS are cancelled and
// old ones deleted on the way, so the table does not collect them.
export async function queueListing({ root, path, requestedBy }) {
  const id = randomUUID();
  await db().transaction(async (tx) => {
    // Both by stage AND work id: "folder-listing" is a name a work could
    // be given, and a real work's chain must never be touched here.
    await tx.execute(sql`update jobs set state = 'cancelled', updated_at = now(),
        error_class = 'cancelled', error_message = 'no worker answered in time'
      where stage = 'list' and work_id = ${LISTING_WORK} and state in ('queued', 'retry_wait')
        and created_at < now() - make_interval(hours => ${LISTING_STALE_HOURS})`);
    await tx.execute(sql`delete from jobs where stage = 'list' and work_id = ${LISTING_WORK}
      and state not in ('queued', 'retry_wait', 'leased', 'running', 'cancel_requested')
      and created_at < now() - make_interval(days => ${LISTING_KEEP_DAYS})`);
    await tx.execute(sql`insert into jobs (id, chain_id, parent_id, stage, capability, idempotency_key, work_id, params, requested_by, max_attempts)
      values (${id}, ${id}, null, 'list', 'source', ${`list:${id}`}, ${LISTING_WORK},
        ${JSON.stringify({ folder: { root, path } })}::jsonb, ${requestedBy}, 2)`);
  });
  return id;
}

const listingRow = (r) => ({
  id: r.id,
  root: r.params?.folder?.root ?? "",
  path: r.params?.folder?.path ?? "",
  state: r.state,
  attempt: r.attempt,
  errorClass: r.error_class,
  errorMessage: r.error_message,
  result: r.state === "committed" ? r.result : null,
  createdAt: r.created_at,
  finishedAt: r.finished_at,
  owner: r.owner,
});

export async function listingById(id) {
  if (!LISTING_UUID.test(String(id))) return null;
  const r = await db().execute(sql`select * from jobs where id = ${id} and stage = 'list' and work_id = ${LISTING_WORK}`);
  return r.rows[0] ? listingRow(r.rows[0]) : null;
}

// The listings of the last week, newest first, for finding the way back
// to one.
export async function recentListings(limit = 12) {
  const r = await db().execute(sql`select * from jobs where stage = 'list' and work_id = ${LISTING_WORK}
    order by created_at desc limit ${limit}`);
  return r.rows.map(listingRow);
}

// Cancels a listing nobody has answered (a wrong folder typed).
export async function cancelListing(id) {
  if (!LISTING_UUID.test(String(id))) return;
  await db().execute(sql`update jobs set state = case when state in ('leased', 'running') then 'cancel_requested' else 'cancelled' end,
      updated_at = now()
    where id = ${id} and stage = 'list' and work_id = ${LISTING_WORK} and state in ('queued', 'retry_wait', 'blocked', 'leased', 'running')`);
}

const nextId = async (tx) => -Number((await tx.execute(sql`select nextval('local_id_seq') as n`)).rows[0].n);
// Shoko's sort name drops a leading article; the Library sorts by it.
const sortName = (name) => name.replace(/^(the|an?)\s+/i, "") || name;

// Finds or makes the local series (and the title it sits under). A series
// is found by its name, capitals aside, so episodes typed with the same
// title land together; `groupTitle` files the series under a wider title
// and, typed for a series that already exists, moves it there. Left empty:
// a new series is its own title, an existing one stays where it is. The
// file's own id is taken by the caller, one per file.
async function placeLocal(tx, { seriesTitle, groupTitle }) {
  const season = (await tx.execute(sql`select id, name, franchise_id from seasons
    where id < 0 and lower(name) = lower(${seriesTitle}) order by id desc limit 1`)).rows[0];
  let franchiseId = season?.franchise_id ?? null;
  const under = groupTitle || (season ? null : seriesTitle);
  if (under) {
    franchiseId = (await tx.execute(sql`select id from franchises
      where id < 0 and lower(name) = lower(${under}) order by id desc limit 1`)).rows[0]?.id ?? null;
    if (franchiseId == null) {
      franchiseId = await nextId(tx);
      await tx.execute(sql`insert into franchises (id, name, sort_name) values (${franchiseId}, ${under}, ${sortName(under)})`);
    }
  }
  let seriesId = season?.id ?? null;
  if (seriesId == null) {
    seriesId = await nextId(tx);
    await tx.execute(sql`insert into seasons (id, franchise_id, name) values (${seriesId}, ${franchiseId}, ${seriesTitle})`);
  } else if (season.franchise_id !== franchiseId) {
    await tx.execute(sql`update seasons set franchise_id = ${franchiseId} where id = ${seriesId}`);
  }
  return { seriesId, seriesTitle: season?.name ?? seriesTitle };
}

// Queues one local file: the checks, the series' placement and the chain's
// rows in one transaction under the operator lock, so two submits cannot
// take one work id for two files, and a submit that queues nothing changes
// nothing. `describe(placed)` builds the job's params from the ids taken.
// Returns { chainId, placed }, or { removing }, { taken } or { existing }
// naming what stopped it.
export async function queueLocal({ root, path, workId, seriesTitle, groupTitle, describe, requestedBy }) {
  return db().transaction(async (tx) => {
    await tx.execute(OPERATOR_LOCK);
    if (await beingRemoved(workId, tx)) return { removing: true };
    // The file's own chain first: adding it again under its default id
    // must say "already here", not "that id is another work's".
    const existing = await existingLocal(root, path, tx);
    if (existing) return { existing };
    if (await workIdTaken(workId, tx)) return { taken: true };
    const placed = { ...(await placeLocal(tx, { seriesTitle, groupTitle })) };
    placed.fileId = await nextId(tx);
    const { chainId } = await insertChain(tx, { key: localKey(root, path), workId, params: describe(placed), requestedBy });
    return { chainId, placed };
  });
}

// Queues several files of one series at once (the folder page), all or
// nothing: every file is checked first, and if any is refused, nothing is
// queued and `problems` says which and why (by the file's path). Each
// file gets its own id and chain; the series is placed once. Returns
// { chainIds, placed } or { problems }.
export async function queueLocalBatch({ root, files, seriesTitle, groupTitle, describe, requestedBy }) {
  return db().transaction(async (tx) => {
    await tx.execute(OPERATOR_LOCK);
    const problems = {};
    const existing = await existingLocalMany(root, files.map((f) => f.path), tx);
    for (const f of files) {
      const e = existing.get(f.path);
      if (e) problems[f.path] = `already queued or in the gallery as “${e.work_id}”`;
      else if (await beingRemoved(f.workId, tx)) problems[f.path] = `“${f.workId}” is still being removed; add it again once its old files are deleted (Jobs)`;
      else if (await workIdTaken(f.workId, tx)) problems[f.path] = `“${f.workId}” is already used by another work; type another`;
    }
    if (Object.keys(problems).length) return { problems };
    const placed = await placeLocal(tx, { seriesTitle, groupTitle });
    const chainIds = [];
    // In the order given (the page sorts by episode number), a millisecond
    // apart, so the workers take them in that order.
    const t0 = Date.now();
    for (const [i, f] of files.entries()) {
      const fileId = await nextId(tx);
      const { chainId } = await insertChain(tx, { key: localKey(root, f.path), workId: f.workId, params: describe({ ...placed, fileId }, f), requestedBy, at: new Date(t0 + i) });
      chainIds.push(chainId);
    }
    return { chainIds, placed };
  });
}

// The pipeline's --source-identity object for a local file: the keys the
// bundle format allows and no others (onboard.js identityFor). The id
// fields carry the gallery's own ids, below zero (ADR-0014).
export function localIdentity({ seriesTitle, seriesId, fileId, entryType, episode, episodeTitle, path, workId }) {
  const identity = {
    series_title: seriesTitle,
    entry_type: entryType,
    source_filename: path.split("/").pop(),
    series_id: seriesId,
    episode_ids: [fileId],
    file_id: fileId,
    work_id: workId,
  };
  if (entryType === "episode") identity.episode = episode;
  if (entryType === "episode" && episodeTitle) identity.episode_title = episodeTitle;
  return identity;
}

// Where the file is, as the job carries it. No hash and no size: the
// worker finds both (hikari_worker/stages.py, `local`).
export function localSource(root, path) {
  return { local: true, fingerprint: null, size: null, locations: [{ root, relative_path: path }], media: {} };
}
