// Repeats across the works of one Shoko series (ADR-0012): openings,
// endings, studio logos, recaps and reused cuts, found by matching each
// work's pool against its siblings' with the embeddings the analyze stage
// already stored. No model runs here and no source is opened.
//
// Two frames match at COSINE or above. A repeated segment plays at one
// time shift between two episodes, so matches are sorted by shift and a
// run of them counts only when each shift is within SHIFT_SECONDS of the
// next and the run holds MIN_RUN different frames of each episode; a lone
// look-alike frame never does. The whole pool is matched, not only the
// picked stills: two episodes rarely pick the same frames of an opening,
// but their pools both cover it.
//
// The result is a mark on the still (stills.repeat_in: the sibling works it
// repeats in). Nothing is hidden or culled by it; the reviewer acts on it,
// one still at a time or with cullRepeats() for a season.
//
// The gallery's worker keeps the marks current in the background: a work is
// matched again whenever its set of siblings changes (works.repeats_key).
import { existsSync, readdirSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { sql } from "drizzle-orm";
import { env } from "$env/dynamic/private";
import { db } from "./db/index.js";
import { runsRoot } from "./runs.js";

export const COSINE = 0.95;
export const SHIFT_SECONDS = 2;
export const MIN_RUN = 3;
// Two real episodes share a few hundred matching frame pairs at most
// (293 on the seasons measured). Far past that the pools are not two
// different works, and the grouping below would hold the server up.
const MAX_MATCHES = 50_000;

const WORK_ID = /^[a-z0-9][a-z0-9-]{1,71}$/;
const yieldNow = () => new Promise((done) => setImmediate(done));

async function readJson(p) {
  return JSON.parse(await readFile(p, "utf8"));
}

function onlyDir(d) {
  const subs = existsSync(d) ? readdirSync(d, { withFileTypes: true }).filter((e) => e.isDirectory()) : [];
  if (subs.length !== 1) throw new Error(`expected one folder under ${d}, found ${subs.length}`);
  return join(d, subs[0].name);
}

// A work's pool from its run records: every frame of the bundle and every
// surplus frame the extraction's audit lists, in time order, as unit
// vectors in one array. The records must be the bundle the work's stills
// came from (candidate ids mean nothing across extractions), and every
// pool frame must have its embedding: part of a pool would be matched,
// marked current and never looked at again.
export async function readPool(workId, bundleId) {
  if (!WORK_ID.test(workId)) throw new Error(`not a work id: ${workId}`);
  const workDir = join(runsRoot(), workId);
  const times = new Map();
  const bundle = await readJson(join(onlyDir(join(workDir, "extract", "prepared")), "manifest.json"));
  if (bundle.bundle_id !== bundleId) throw new Error(`the run records hold bundle ${bundle.bundle_id}, the gallery holds ${bundleId}`);
  for (const c of bundle.candidates || []) {
    const [num, den] = typeof c.time_base === "string" ? c.time_base.split("/") : c.time_base || [1, 1];
    times.set(c.candidate_id, (c.intended_pts * Number(num)) / Number(den));
  }
  const audit = join(workDir, "extract", "audit", "breadth-omitted-candidates.json");
  if (existsSync(audit)) {
    let rows = await readJson(audit);
    rows = Array.isArray(rows) ? rows : Object.values(rows)[0];
    for (const r of rows || []) if (!times.has(r.candidate_id) && r.timestamp_seconds != null) times.set(r.candidate_id, r.timestamp_seconds);
  }
  const files = [];
  for (const lane of ["embeddings", "surplus-embeddings"]) {
    const dir = join(workDir, "analyze", lane, "artifacts");
    if (!existsSync(dir)) continue;
    for (const name of readdirSync(dir)) if (name.endsWith(".embedding.json")) files.push(join(dir, name));
  }
  const frames = [];
  const seen = new Set();
  // A few files at a time: the records are on the array, and the server
  // keeps answering pages meanwhile.
  for (let at = 0; at < files.length; at += 16) {
    const batch = await Promise.all(files.slice(at, at + 16).map(readJson));
    for (const a of batch) {
      const ts = times.get(a.candidate_id);
      if (ts == null || !Array.isArray(a.embedding) || seen.has(a.candidate_id)) continue;
      seen.add(a.candidate_id);
      frames.push({ id: a.candidate_id, ts, v: a.embedding });
    }
  }
  if (!frames.length) throw new Error(`no pool embeddings for ${workId}`);
  if (seen.size < times.size) throw new Error(`${times.size - seen.size} of ${times.size} pool frames have no embedding in the analyze record`);
  frames.sort((x, y) => x.ts - y.ts || x.id.localeCompare(y.id));
  const dim = frames[0].v.length;
  const emb = new Float32Array(frames.length * dim);
  frames.forEach((f, i) => {
    if (f.v.length !== dim) throw new Error(`${workId}/${f.id}: embedding of ${f.v.length} numbers among ${dim}`);
    let norm = 0;
    for (const x of f.v) norm += x * x;
    norm = Math.sqrt(norm) || 1;
    for (let k = 0; k < dim; k++) emb[i * dim + k] = f.v[k] / norm;
  });
  return { ids: frames.map((f) => f.id), ts: Float64Array.from(frames, (f) => f.ts), emb, dim, n: frames.length };
}

// Which frames of two pools sit in a repeated run they share. Returns one
// flag per frame of each. Yields to the event loop as it goes: this runs in
// the web server's process.
export async function matchPools(a, b) {
  const fa = new Uint8Array(a.n);
  const fb = new Uint8Array(b.n);
  if (a.dim !== b.dim) return { fa, fb };
  const d = a.dim;
  const ea = a.emb;
  const eb = b.emb;
  const hits = [];
  let since = performance.now();
  for (let i = 0; i < a.n; i++) {
    const ai = i * d;
    for (let j = 0; j < b.n; j++) {
      const bj = j * d;
      let dot = 0;
      for (let k = 0; k < d; k++) dot += ea[ai + k] * eb[bj + k];
      if (dot >= COSINE) hits.push({ i, j, shift: b.ts[j] - a.ts[i] });
    }
    if (hits.length > MAX_MATCHES) throw new Error(`more than ${MAX_MATCHES} matching frame pairs: the two pools are too alike to be two works`);
    if (performance.now() - since > 12) {
      await yieldNow();
      since = performance.now();
    }
  }
  hits.sort((x, y) => x.shift - y.shift);
  // A run of matches whose shifts follow each other within SHIFT_SECONDS.
  let lo = 0;
  for (let hi = 1; hi <= hits.length; hi++) {
    if (hi < hits.length && hits[hi].shift - hits[hi - 1].shift <= SHIFT_SECONDS) continue;
    const run = hits.slice(lo, hi);
    if (Math.min(new Set(run.map((h) => h.i)).size, new Set(run.map((h) => h.j)).size) >= MIN_RUN) {
      for (const h of run) {
        fa[h.i] = 1;
        fb[h.j] = 1;
      }
    }
    lo = hi;
  }
  return { fa, fb };
}

// Pools and pair results are kept in memory between passes: a new episode
// makes every sibling due again, and only its own pairs are new work. A
// pool changes only with a new extraction, which has a new bundle id.
const pools = new Map(); // work id -> { bundle, pool }
const POOL_FRAMES = 20_000; // ~60 MB of vectors
const pairs = new Map(); // "<a>@<bundle>|<b>@<bundle>" -> { fa, fb }
const PAIRS = 4_000;

async function poolOf(work) {
  const hit = pools.get(work.id);
  if (hit && hit.bundle === work.bundle_id) {
    pools.delete(work.id);
    pools.set(work.id, hit); // most recently used last
    return hit.pool;
  }
  const pool = await readPool(work.id, work.bundle_id);
  pools.delete(work.id);
  pools.set(work.id, { bundle: work.bundle_id, pool });
  let total = 0;
  for (const p of pools.values()) total += p.pool.n;
  for (const [id, p] of pools) {
    if (total <= POOL_FRAMES || id === work.id) break;
    pools.delete(id);
    total -= p.pool.n;
  }
  return pool;
}

async function flagsAgainst(work, pool, sibling, siblingPool) {
  const ka = `${work.id}@${work.bundle_id}`;
  const kb = `${sibling.id}@${sibling.bundle_id}`;
  const fwd = pairs.get(`${ka}|${kb}`);
  if (fwd) return fwd.fa;
  const back = pairs.get(`${kb}|${ka}`);
  if (back) return back.fb;
  const result = await matchPools(pool, siblingPool);
  pairs.set(`${ka}|${kb}`, result);
  if (pairs.size > PAIRS) pairs.delete(pairs.keys().next().value);
  return result.fa;
}

// One key per series as it stands: a hash of every work's id and bundle.
// A work with no Shoko series is its own group.
const seriesKeys = sql`select coalesce(shoko_series_id::text, 'w:' || id) as grp,
    md5(string_agg(id || ':' || coalesce(bundle_id, ''), ',' order by id)) as key
  from works group by 1`;

// The works whose marks are out of date.
const due = sql`select w.id, w.bundle_id, w.shoko_series_id, k.key
  from works w join (${seriesKeys}) k on k.grp = coalesce(w.shoko_series_id::text, 'w:' || w.id)
  where w.repeats_key is distinct from k.key`;

// A work whose records could not be read is tried again later, not on
// every pass: work id -> { at, message }.
const failed = new Map();
const RETRY_MS = 10 * 60_000;

// The works not matched against their current siblings yet, and of those
// the ones the last try could not read, with why. A gallery without the
// run records never matches anything, so nothing is due there.
export async function repeatsState() {
  if (!env.HIKARI_RUNS) return { due: [], failed: {} };
  const ids = (await db().execute(sql`${due}`)).rows.map((r) => r.id);
  return { due: ids, failed: Object.fromEntries(ids.filter((id) => failed.has(id)).map((id) => [id, failed.get(id).message])) };
}

async function refreshWork(work) {
  const started = Date.now();
  const siblings = work.shoko_series_id == null ? [] : (await db().execute(sql`select id, bundle_id from works
    where shoko_series_id = ${work.shoko_series_id} and id <> ${work.id} order by id`)).rows;
  const marks = {};
  const unread = [];
  let frames = 0;
  if (siblings.length) {
    const pool = await poolOf(work);
    frames = pool.n;
    for (const s of siblings) {
      let theirs;
      try {
        theirs = await poolOf(s);
      } catch (error) {
        unread.push(`${s.id} (${error.message})`);
        continue;
      }
      const flags = await flagsAgainst(work, pool, s, theirs);
      for (let i = 0; i < pool.n; i++) if (flags[i]) (marks[pool.ids[i]] ??= []).push(s.id);
    }
  }
  const json = JSON.stringify(marks);
  await db().transaction(async (tx) => {
    // The key first: it takes the work's row lock, so a pool frame pinned
    // meanwhile (pool.pinPoolFrame clears the key in its own transaction)
    // either lands before the marks are written and gets one, or clears
    // the key after this commits and the work is matched again. With a
    // sibling unread the marks are the best available and the work stays
    // due, so it is matched again once that sibling can be read.
    if (!unread.length) await tx.execute(sql`update works set repeats_key = ${work.key} where id = ${work.id}`);
    await tx.execute(sql`update stills set repeat_in = coalesce(${json}::jsonb -> candidate_id, '[]'::jsonb)
      where work_id = ${work.id} and repeat_in is distinct from coalesce(${json}::jsonb -> candidate_id, '[]'::jsonb)`);
  });
  if (unread.length) throw new Error(`matched without ${unread.join(", ")}`);
  return { frames, flagged: Object.keys(marks).length, siblings: siblings.length, ms: Date.now() - started };
}

// One background pass: bring due works up to date, newest import first,
// for at most budgetMs. Returns how many were done.
export async function refreshRepeats(budgetMs = 8_000) {
  const started = Date.now();
  let done = 0;
  while (Date.now() - started < budgetMs) {
    const rows = (await db().execute(sql`${due} order by w.imported_at desc, w.id`)).rows;
    const stale = new Set(rows.map((r) => r.id));
    for (const id of failed.keys()) if (!stale.has(id)) failed.delete(id); // removed, or no longer due
    const work = rows.find((r) => !(failed.get(r.id)?.at > Date.now()));
    if (!work) break;
    try {
      const r = await refreshWork(work);
      failed.delete(work.id);
      done += 1;
      if (r.siblings) console.log(`[gallery-worker] repeats ${work.id}: ${r.flagged} of ${r.frames} pool frames repeat in other works of its series (${r.siblings} checked, ${r.ms} ms)`);
    } catch (error) {
      failed.set(work.id, { at: Date.now() + RETRY_MS, message: error.message });
      console.error(`[gallery-worker] repeats ${work.id}: ${error.message}; trying again in ${RETRY_MS / 60_000} min`);
    }
  }
  return done;
}

// --- What the Review page does with the marks ---------------------------

// The series whose every work is matched against its current siblings,
// with how many other works each holds. Marks in a series that is not
// settled may still change, so nothing below counts or culls there; the
// check is part of each statement, never a separate read. A statement sees
// the series as it stood when it started: a work imported while a cull
// runs does not undo what was eligible when the statement started.
const settled = sql`select w.shoko_series_id as sid, count(*) - 1 as others
  from works w join (${seriesKeys}) k on k.grp = w.shoko_series_id::text
  where w.shoko_series_id is not null
  group by 1 having bool_and(w.repeats_key is not distinct from k.key)`;
// $lib/repeats.js seasonWide() in SQL (s = stills, o = settled).
const SEASON_WIDE = sql`jsonb_array_length(s.repeat_in) >= greatest(2, ceil(o.others / 2.0))`;
// What the bulk cull takes: picked, nobody has reviewed it, not hidden,
// not locked (a lock is how the owner keeps one), repeating season-wide.
const CULLABLE = sql`s.selected and s.review_state = 'unreviewed' and not s.excluded and not s.locked and ${SEASON_WIDE}`;

// work id -> how many of its stills "Cull the repeats" would take now.
export async function cullableRepeats() {
  const r = await db().execute(sql`select s.work_id, count(*)::int as n
    from stills s join works w on w.id = s.work_id join (${settled}) o on o.sid = w.shoko_series_id
    where ${CULLABLE} group by 1`);
  return new Map(r.rows.map((x) => [x.work_id, x.n]));
}

// Cull a season's openings, endings and logos in one go: every still in
// these works that nobody has reviewed and that repeats season-wide.
// Locked, kept, culled and hidden stills are not changed. Returns the
// count and the batch's timestamp, which undoCull uses.
export async function cullRepeats(workIds) {
  if (!workIds.length) return { count: 0, at: null };
  const r = await db().execute(sql`update stills s set review_state = 'culled', reviewed_at = now()
    from works w join (${settled}) o on o.sid = w.shoko_series_id
    where w.id = s.work_id and s.work_id in ${workIds} and ${CULLABLE}
    returning s.reviewed_at::text as at`);
  return { count: r.rows.length, at: r.rows[0]?.at ?? null };
}

// Undoes one cullRepeats batch: only stills still culled at exactly that
// batch's time, so one reviewed or edited since keeps what was done to it.
export async function undoCull(workIds, at) {
  if (!workIds.length || !at) return 0;
  const r = await db().execute(sql`update stills set review_state = 'unreviewed', reviewed_at = null
    where work_id in ${workIds} and review_state = 'culled' and reviewed_at = ${at}::timestamptz
    returning id`);
  return r.rows.length;
}
