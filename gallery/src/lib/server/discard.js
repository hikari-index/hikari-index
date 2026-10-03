// "Done reviewing": discard a work's extra frames (ADR-0009). Extraction
// keeps every frame that cleared the quality floor beside the bundle as
// surplus, so the pick and the review can reach them. Once the owner has
// reviewed a work, the extra frames' pixels go and their records stay: one
// `discard` job per work for the source worker deletes, in that work's
// surplus folder, every frame that is not a picked still (any review state)
// or a locked one. Works imported before the job table have no surplus
// folder here and are never offered. After a discard the work cannot be
// re-run: the picker could choose a frame that is gone.
import { createHash, randomUUID } from "node:crypto";
import { lstat, readdir } from "node:fs/promises";
import { join, resolve } from "node:path";
import { sql } from "drizzle-orm";
import { env } from "$env/dynamic/private";
import { db } from "$lib/server/db/index.js";
import { OPERATOR_LOCK } from "$lib/server/jobs.js";
import { shownEpisodeTitle } from "$lib/titles.js";
import { WORK_ID } from "$lib/server/onboard.js";

export const SCOPES = new Set(["work", "season"]);
const FRAME = /^cand-\d{4}\.png$/;
const SEASON_ID = /^-?[1-9]\d{0,8}$/; // below zero: a local series (ADR-0014)
// A stage of the work that has not ended: a re-run in flight could pick a
// frame the discard would delete, so nothing is discarded while one exists.
const OPEN = ["queued", "leased", "running", "retry_wait", "blocked", "cancel_requested"];
// The confirm token: what the page showed for each work that can go (its
// surplus folder, the frames it keeps, and every extra frame by name and
// size). The same digest is computed again inside the transaction, so a
// re-run, an import or any change in the folder between the page and the
// click refuses it; the job then carries that exact list of files.
const digest = (works) => createHash("sha256").update(JSON.stringify(works.map((w) => [w.id, w.surplus, [...w.keep].sort(), w.frames?.list ?? null, w.frames?.kept ?? null]))).digest("hex").slice(0, 32);


// The surplus folder's path under /runs, from the work's newest committed
// extract, checked to be inside runs/<work>/ before it is ever joined.
function surplusRel(workId, extractResult) {
  const rel = extractResult?.surplus_dir;
  if (typeof rel !== "string" || !WORK_ID.test(workId)) return null;
  const parts = rel.split("/");
  if (parts.length !== 5 || parts[0] !== "runs" || parts[1] !== workId || parts[2] !== "extract" || parts[3] !== "surplus" || !parts[4] || parts[4].startsWith(".")) return null;
  return rel;
}

// Frames in the folder now: kept and extra counts and bytes, and the exact
// list of extra files (name and size) the discard would delete. Null when
// the folder cannot be fully read: a folder that cannot be inventoried is
// never discarded on the strength of a partial look.
async function frames(rel, keep) {
  const dir = join(resolve(env.HIKARI_RUNS || "/runs"), rel.replace(/^runs\//, ""));
  const out = { kept: 0, extra: 0, keptBytes: 0, extraBytes: 0, present: true, list: [] };
  let names;
  try {
    names = await readdir(dir);
  } catch (e) {
    if (e.code === "ENOENT") return { ...out, present: false };
    return null;
  }
  for (const name of names.sort()) {
    if (!FRAME.test(name)) continue;
    let st;
    try {
      st = await lstat(join(dir, name));
    } catch {
      return null;
    }
    if (!st.isFile()) continue;
    if (keep.has(name.slice(0, -4))) {
      out.kept += 1;
      out.keptBytes += st.size;
    } else {
      out.extra += 1;
      out.extraBytes += st.size;
      out.list.push([name, st.size]);
    }
  }
  return out;
}

async function describe(q, scope, target, { lock = false } = {}) {
  if (scope === "season" && !SEASON_ID.test(String(target))) return [];
  const works = scope === "work"
    ? (await q.execute(sql`select * from works where id = ${target}`)).rows
    : (await q.execute(sql`select * from works where shoko_series_id = ${Number(target)} order by episode, id`)).rows;
  if (!works.length) return [];
  const ids = works.map((w) => w.id);
  const counts = new Map((await q.execute(sql`select work_id,
      count(*) filter (where selected and review_state = 'unreviewed' and not excluded)::int as unreviewed,
      count(*) filter (where selected)::int as picked,
      count(*) filter (where locked and not selected and jsonb_array_length(tiers) = 0)::int as pending
    from stills where work_id in ${ids} group by work_id`)).rows.map((r) => [r.work_id, r]));
  const keep = new Map();
  for (const r of (await q.execute(sql`select work_id, candidate_id from stills
      where work_id in ${ids} and (source = 'surplus' or locked)`)).rows) {
    if (!keep.has(r.work_id)) keep.set(r.work_id, new Set());
    keep.get(r.work_id).add(r.candidate_id);
  }
  // Every job of these works: the newest committed extract names the
  // surplus folder, the newest discard says where a discard stands, and
  // any open stage at all blocks (see OPEN).
  const jobs = (await q.execute(sql`select work_id, stage, state, attempt, result, lease_expires_at, finished_at from jobs
    where work_id in ${ids} order by created_at ${lock ? sql`for update` : sql``}`)).rows;
  return works.map((w) => {
    const own = jobs.filter((j) => j.work_id === w.id);
    const extract = own.filter((j) => j.stage === "extract" && j.state === "committed").at(-1);
    const discard = own.filter((j) => j.stage === "discard" && (j.state !== "cancelled" || j.attempt > 0)).at(-1);
    const open = own.filter((j) => j.stage !== "discard" && OPEN.includes(j.state)).map((j) => j.stage);
    const c = counts.get(w.id) ?? { unreviewed: 0, picked: 0, pending: 0 };
    const ep = w.entry_type === "episode" && w.episode != null ? `E${String(w.episode).padStart(2, "0")}` : null;
    return {
      id: w.id,
      label: [w.title, ep, shownEpisodeTitle(w.entry_type, w.episode_title_human ?? w.episode_title)].filter(Boolean).join(" · "),
      picked: c.picked,
      unreviewed: c.unreviewed,
      pending: c.pending ?? 0,
      surplus: surplusRel(w.id, extract?.result),
      open,
      keep: keep.get(w.id) ?? new Set(),
      discard: discard ? { state: discard.state, attempt: discard.attempt, result: discard.result, finishedAt: discard.finished_at } : null,
      identity: { series_title: w.title, entry_type: w.entry_type, episode: w.episode, episode_title: w.episode_title_human ?? w.episode_title },
    };
  });
}

// Why a work is not offered now, or null when it can be discarded.
export function blocker(w) {
  if (w.discard?.state === "committed") return "extra frames already discarded";
  if (w.discard && ["blocked", "dead_letter", "ineligible", "cancelled"].includes(w.discard.state)) return "its last discard did not finish; Retry it on Jobs";
  if (w.discard) return "discarding now";
  if (w.open.length) return `a stage of it is still open (${w.open.join(", ")}); wait for it to finish`;
  if (w.pending) return `${w.pending} locked pool frame${w.pending === 1 ? "" : "s"} wait${w.pending === 1 ? "s" : ""} for a re-run; re-run the work first (a re-run is refused after a discard)`;
  if (!w.surplus) return "no extra frames recorded for it";
  if (w.unreviewed) return `${w.unreviewed} still${w.unreviewed === 1 ? "" : "s"} left to review`;
  return null;
}

export async function discardPreview(scope, target) {
  if (!SCOPES.has(scope) || !target) return null;
  const q = db();
  const works = await describe(q, scope, target);
  if (!works.length) return null;
  for (const w of works) await inventory(w);
  const expect = digest(works.filter((w) => !w.blocker));
  for (const w of works) {
    w.keep = [...w.keep].sort();
    if (w.frames) w.frames = { ...w.frames, list: undefined }; // the page shows counts
  }
  const label = scope === "season"
    ? (await q.execute(sql`select name from seasons where id = ${Number(target)}`)).rows[0]?.name ?? works[0].identity.series_title
    : works[0].label;
  return { scope, target, label, works, expect };
}

// Reads the work's folder and settles what blocks it. A folder that could
// not be read blocks: nothing is discarded on the strength of a partial look.
async function inventory(w) {
  w.blocker = blocker(w);
  w.frames = w.surplus ? await frames(w.surplus, w.keep) : null;
  if (!w.blocker && w.surplus) {
    if (!w.frames) w.blocker = "its surplus folder could not be read";
    else if (!w.frames.present) w.blocker = "its surplus folder is not on the share";
    else if (!w.frames.extra) w.blocker = "no extra frames left to delete";
  }
}

// Queues the discard for every work in scope that can be discarded; the
// rest are skipped and reported. `expect` is the page's confirm token.
export async function discardScope({ scope, target, expect, requestedBy }) {
  if (!SCOPES.has(scope) || !target) return { refused: "Nothing to discard." };
  return db().transaction(async (tx) => {
    // One operator action at a time (jobs.js OPERATOR_LOCK): a re-run or
    // retry cannot slip a stage in while this decides.
    await tx.execute(OPERATOR_LOCK);
    const works = await describe(tx, scope, target, { lock: true });
    for (const w of works) await inventory(w);
    const ready = works.filter((w) => !w.blocker);
    if (!ready.length) return { refused: "Nothing here can be discarded now (reviewed works with extra frames still on disk)." };
    if (digest(ready) !== String(expect ?? "")) {
      return { refused: "What this would discard changed since the page was shown (a stage started, stills changed, or files on disk did). Nothing was discarded; look at the list again." };
    }
    for (const w of ready) {
      const chainId = randomUUID();
      await tx.execute(sql`insert into jobs (id, chain_id, stage, capability, idempotency_key, work_id, params, requested_by)
        values (${chainId}, ${chainId}, 'discard', 'source', ${`discard:${w.id}:${chainId}`}, ${w.id},
          ${JSON.stringify({ surplus_dir: w.surplus, keep: [...w.keep].sort(), delete: w.frames.list.map(([name]) => name), identity: w.identity })}::jsonb, ${requestedBy})`);
    }
    return { works: ready.length, skipped: works.length - ready.length };
  });
}

// True once a discard of the work's extra frames exists that may have
// deleted anything (queued, or started at least once): a re-run could
// then pick a frame that is gone. A discard cancelled before it started
// does not count.
export async function surplusDiscarded(workId) {
  const r = await db().execute(sql`select 1 from jobs where work_id = ${workId} and stage = 'discard'
    and (state <> 'cancelled' or attempt > 0) limit 1`);
  return r.rows.length > 0;
}

// For the Review tree: per work, whether "done reviewing" is on offer or
// what its discard did.
export async function discardStates() {
  const r = await db().execute(sql`select distinct on (work_id) work_id, state, (result ->> 'bytes')::bigint as bytes
    from jobs where stage = 'discard' order by work_id, created_at desc`);
  return new Map(r.rows.map((x) => [x.work_id, { state: x.state, bytes: x.bytes == null ? null : Number(x.bytes) }]));
}
