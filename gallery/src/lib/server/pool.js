// The extraction's pool for a work: every frame the picker could have
// chosen (the prepared bundle plus the surplus beside it), read from the
// run records. The workbench's pool view lists the ones that never became
// stills, with a preview made from the master on request (ADR-0008
// operator pin, second half: a frame the picker never chose).
import { constants, existsSync, lstatSync, readdirSync, readFileSync, realpathSync } from "node:fs";
import { open } from "node:fs/promises";
import { join, resolve, sep } from "node:path";
import { eq, sql } from "drizzle-orm";
import { env } from "$env/dynamic/private";
import { db, schema } from "./db/index.js";
import { OPERATOR_LOCK } from "./jobs.js";

const { stills: stillsTable } = schema;
const WORK_ID = /^[a-z0-9][a-z0-9-]{1,71}$/;
const CANDIDATE = /^cand-\d{4}$/;
const runsRoot = () => resolve(env.HIKARI_RUNS || "/runs");
const readJson = (p) => JSON.parse(readFileSync(p, "utf8"));

// A path whose every component, resolved, still sits under the runs
// root and is a regular file: a link planted anywhere along it leads
// nowhere. Returns the resolved path, or null.
function insideRuns(p) {
  let real;
  let root;
  try {
    real = realpathSync(p);
    root = realpathSync(runsRoot());
  } catch {
    return null;
  }
  if (!real.startsWith(root + sep)) return null;
  try {
    if (!lstatSync(real).isFile()) return null;
  } catch {
    return null;
  }
  return real;
}

function workRoot(workId) {
  if (!WORK_ID.test(workId)) return null;
  const dir = resolve(runsRoot(), workId);
  return dir.startsWith(runsRoot() + sep) ? dir : null;
}

function onlyDir(d) {
  if (!existsSync(d)) return null;
  const subs = readdirSync(d, { withFileTypes: true }).filter((e) => e.isDirectory());
  return subs.length === 1 ? join(d, subs[0].name) : null;
}

function readFrames(workId) {
  const root = workRoot(workId);
  if (!root) return [];
  const bundleDir = onlyDir(join(root, "extract", "prepared"));
  if (!bundleDir) return [];
  const out = [];
  const bundle = readJson(join(bundleDir, "manifest.json"));
  for (const c of bundle.candidates || []) {
    if (!CANDIDATE.test(c.candidate_id)) continue;
    const [num, den] = typeof c.time_base === "string" ? c.time_base.split("/") : c.time_base || [1, 1];
    const master = insideRuns(resolve(bundleDir, c.artifact_name || ""));
    if (!master || !master.startsWith(realpathSync(bundleDir) + sep)) continue;
    out.push({ candidate: c.candidate_id, ts: (c.intended_pts * Number(num)) / Number(den), shot: c.shot_id ?? null, source: "published", master });
  }
  // Surplus frames: only those the extraction's audit recorded, and only
  // as regular files in the surplus folder.
  const surplusDir = onlyDir(join(root, "extract", "surplus"));
  const audit = join(root, "extract", "audit", "breadth-omitted-candidates.json");
  if (surplusDir && existsSync(audit)) {
    let rows = readJson(audit);
    rows = Array.isArray(rows) ? rows : Object.values(rows)[0];
    const surplusReal = realpathSync(surplusDir);
    for (const r of rows || []) {
      if (!CANDIDATE.test(r.candidate_id ?? "")) continue;
      const master = insideRuns(join(surplusDir, `${r.candidate_id}.png`));
      if (!master || !master.startsWith(surplusReal + sep)) continue;
      out.push({ candidate: r.candidate_id, ts: r.timestamp_seconds ?? null, shot: r.shot_id ?? null, source: "surplus", master });
    }
  }
  // Held frames (#47): inside an opening or ending chapter, extracted into
  // the surplus and kept out of the pick. Listed with why, so the pool
  // page can say so; a lock brings one in like any surplus frame.
  const heldAudit = join(root, "extract", "audit", "held-candidates.json");
  if (surplusDir && existsSync(heldAudit)) {
    const doc = readJson(heldAudit);
    const surplusReal = realpathSync(surplusDir);
    for (const r of (doc && doc.pixel_artifacts_published && doc.candidates) || []) {
      if (!r.pixel || !CANDIDATE.test(r.candidate_id ?? "")) continue;
      const master = insideRuns(join(surplusDir, `${r.candidate_id}.png`));
      if (!master || !master.startsWith(surplusReal + sep)) continue;
      out.push({ candidate: r.candidate_id, ts: r.timestamp_seconds ?? null, shot: r.shot_id ?? null, source: "surplus", master, held: r.reason === "ending_theme" ? "ending" : r.reason === "opening_theme" ? "opening" : "held" });
    }
  }
  out.sort((a, b) => (a.ts ?? 1e9) - (b.ts ?? 1e9) || a.candidate.localeCompare(b.candidate));
  return out;
}

// The pool, cached a minute per work: sixty preview requests for one page
// must not each walk the run records again.
const inventory = new Map();
const INVENTORY_MS = 60_000;
export function poolFrames(workId) {
  const hit = inventory.get(workId);
  if (hit && Date.now() - hit.at < INVENTORY_MS) return hit.frames;
  const frames = readFrames(workId);
  inventory.set(workId, { at: Date.now(), frames });
  if (inventory.size > 50) inventory.delete(inventory.keys().next().value);
  return frames;
}

// The pool frames that are not stills (picked or kept as not picked).
export async function poolNotInGallery(workId) {
  const frames = poolFrames(workId);
  if (!frames.length) return { frames: [], pool: 0 };
  const have = new Set((await db().select({ c: stillsTable.candidateId }).from(stillsTable).where(eq(stillsTable.workId, workId))).map((r) => r.c));
  return { frames: frames.filter((f) => !have.has(f.candidate)), pool: frames.length };
}

// One frame's master, for the preview route; null unless it is a pool
// frame of this work. The path is checked again now (the inventory is a
// minute old at most and a cached check authorises nothing), then opened
// without following a link at the leaf; its identity (path, mtime, size)
// comes from that handle, and its bytes are read from it only when
// need(key) says the preview is not already in hand.
export async function poolMaster(workId, candidate, need = () => true) {
  if (!CANDIDATE.test(candidate)) return null;
  const f = poolFrames(workId).find((x) => x.candidate === candidate);
  if (!f) return null;
  const real = insideRuns(f.master);
  if (!real || real !== f.master) return null;
  let fh;
  try {
    fh = await open(real, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    const info = await fh.stat();
    if (!info.isFile()) return null;
    const key = `${real}@${info.mtimeMs}@${info.size}`;
    return { key, bytes: need(key) ? await fh.readFile() : null };
  } catch {
    return null;
  } finally {
    await fh?.close();
  }
}

// Pin a pool frame: a still row that is not picked but locked, which the
// next re-run's picker keeps (jobs.rerunFromAnalyze passes locked
// candidates as pins); analyze, derive and import then fill it in like any
// other still. Under the operator lock, and refused once the work has a
// discard (queued or run): a discard deletes surplus frames by a list made
// when it was queued, and a re-run is refused after one, so a pin after it
// could never be brought in. Returns "ok", "gone" or "discarded".
export async function pinPoolFrame(workId, candidate) {
  if (!CANDIDATE.test(candidate)) return "gone";
  return db().transaction(async (tx) => {
    await tx.execute(OPERATOR_LOCK);
    const d = await tx.execute(sql`select 1 from jobs where work_id = ${workId} and stage = 'discard'
      and (state <> 'cancelled' or attempt > 0) limit 1`);
    if (d.rows.length) return "discarded";
    // From the run records as they are now, not the minute-old inventory.
    const f = readFrames(workId).find((x) => x.candidate === candidate);
    if (!f) return "gone";
    // A new row has no repeat mark, so the work is matched again
    // (repeats.js). Before the row is written: the refresh locks the work
    // and then its stills, and this must take them in the same order.
    await tx.execute(sql`update works set repeats_key = null where id = ${workId}`);
    await tx.execute(sql`insert into stills (id, work_id, candidate_id, shot_id, ts_seconds, source, selected, locked, facets, tags, tiers)
      values (${`${workId}/${candidate}`}, ${workId}, ${candidate}, ${f.shot}, ${f.ts}, ${f.source}, false, true, '{}'::jsonb, '[]'::jsonb, '[]'::jsonb)
      on conflict (id) do update set locked = true`);
    return "ok";
  });
}

// A lock on a still with no web images yet (a pool frame) waits for a
// re-run, which is refused after a discard; so such a lock is refused once
// the work has a discard, under the operator lock that discard takes.
// Locks on stills that have web images are plain marks and always allowed.
// Returns {changed, refused}: the ids updated, and the ids a lock was
// refused for by that rule (ids that are not stills are in neither).
export async function setLocks(ids, value) {
  if (!ids.length) return { changed: [], refused: [] };
  return db().transaction(async (tx) => {
    await tx.execute(OPERATOR_LOCK);
    if (!value) {
      return { changed: (await tx.execute(sql`update stills set locked = false where id in ${ids} returning id`)).rows.map((r) => r.id), refused: [] };
    }
    const discarded = sql`exists (select 1 from jobs j where j.work_id = s.work_id
        and j.stage = 'discard' and (j.state <> 'cancelled' or j.attempt > 0))`;
    const refused = (await tx.execute(sql`select s.id from stills s where s.id in ${ids}
      and jsonb_array_length(s.tiers) = 0 and ${discarded}`)).rows.map((r) => r.id);
    const changed = (await tx.execute(sql`update stills s set locked = true where s.id in ${ids}
      and not (jsonb_array_length(s.tiers) = 0 and ${discarded}) returning s.id`)).rows.map((r) => r.id);
    return { changed, refused };
  });
}

// Locked pool frames still waiting for a re-run (no web images yet): a
// discard must not run while any exist, since the re-run that brings
// them in is refused after a discard.
export async function pendingPins(workIds) {
  if (!workIds.length) return new Map();
  const r = await db().execute(sql`select work_id, count(*)::int as n from stills
    where work_id in ${workIds} and locked and not selected and jsonb_array_length(tiers) = 0 group by work_id`);
  return new Map(r.rows.map((x) => [x.work_id, x.n]));
}
