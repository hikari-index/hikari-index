// The job table's server side for the admin pages (research/01 "Recoverable
// job protocol"). Pages only insert and cancel; workers lease, run and
// commit.
import { randomUUID } from "node:crypto";
import { asc, desc, eq, sql } from "drizzle-orm";
import { db, schema } from "./db/index.js";
import { shownEpisodeTitle } from "$lib/titles.js";

const { jobs, workers } = schema;

// What one onboarding does, in order. Each stage waits for the one before.
export const STAGES = [
  { stage: "extract", capability: "source", label: "Find the shots and pull frames from the video" },
  { stage: "analyze", capability: "analyze", label: "Describe each frame and pick the stills" },
  { stage: "derive", capability: "source", label: "Make the web images for the picked stills" },
  { stage: "import", capability: "gallery", label: "Add them to the gallery" },
];
// Read after "the": "the gallery", "once the source worker checks in". The
// source worker is the one that mounts the video (capability "source";
// migration 0014 renamed it from an older name).
export const MACHINE = { source: "source worker", analyze: "analyze worker", gallery: "gallery" };

// The settings an onboarding runs under. A change here is a new run, not a
// rerun: it is part of every idempotency key.
export const POLICY = { extraction: "extraction-policy-v3", budget: "derived" };

const LIVE_MS = 10 * 60 * 1000; // a worker that polled within this is on
// Operator actions that read the job table and then change it (re-run,
// retry, discard, removal) take this lock first, so none of them decides
// on rows another is still inserting. Workers never take it.
export const OPERATOR_LOCK = sql`select pg_advisory_xact_lock(hashtext('hikari-index:operator'))`;
const OPEN = ["queued", "leased", "running", "retry_wait", "blocked", "cancel_requested"];

export function rootKey(fingerprint, size) {
  return `extract:${fingerprint}:${size}:${POLICY.extraction}:${POLICY.budget}`;
}

// Chains that ended without a result; the same source may be onboarded again.
export const RETRYABLE_END = ["cancelled", "dead_letter"];

// Creates the whole chain, or finds the one this source already has: the
// first stage's key is unique, so a second click or a double submit lands
// on the existing row. A chain that was cancelled or gave up keeps its rows
// (evidence is never overwritten) but frees its keys, so the operator can
// start the source over. Returns { chainId, created }. `key` replaces the
// hash-and-size key for a file nobody has hashed yet (a local file,
// ADR-0014: keyed by its folder and path). `at` is the chain's creation
// time: a batch queued in one transaction would otherwise share one
// timestamp and run in random order, and a season should run in episode
// order (the workers take the oldest runnable stage first).
export async function enqueueOnboarding({ workId, params, fingerprint, size, requestedBy, key: given = null }) {
  const key = given ?? rootKey(fingerprint, size);
  return db().transaction((tx) => insertChain(tx, { key, workId, params, requestedBy })).catch(async (error) => {
    const [existing] = await db().select({ chainId: jobs.chainId }).from(jobs).where(eq(jobs.idempotencyKey, key));
    if (existing) return { chainId: existing.chainId, created: false };
    throw error;
  });
}

// The chain's rows, inside the caller's transaction (a local file's
// queueing also places its series there, local.js). Rolls the transaction
// back when an identical request got in first.
export async function insertChain(tx, { key, workId, params, requestedBy, at = null }) {
  const [existing] = await tx.select({ chainId: jobs.chainId, state: jobs.state }).from(jobs).where(eq(jobs.idempotencyKey, key));
  if (existing && !RETRYABLE_END.includes(existing.state)) return { chainId: existing.chainId, created: false };
  if (existing) {
    await tx
      .update(jobs)
      .set({ idempotencyKey: sql`${jobs.idempotencyKey} || '#' || ${jobs.chainId}::text`, updatedAt: sql`now()` })
      .where(eq(jobs.chainId, existing.chainId));
  }
  const chainId = randomUUID();
  let parentId = null;
  for (const [i, s] of STAGES.entries()) {
    const id = i === 0 ? chainId : randomUUID();
    const [row] = await tx
      .insert(jobs)
      .values({
        id,
        chainId,
        parentId,
        stage: s.stage,
        capability: s.capability,
        idempotencyKey: i === 0 ? key : `${s.stage}:${key}`,
        workId,
        params,
        requestedBy,
        ...(at ? { createdAt: at, updatedAt: at } : {}),
      })
      .onConflictDoNothing()
      .returning({ id: jobs.id });
    if (!row) {
      // Lost a race with an identical request; that one's chain stands.
      tx.rollback();
    }
    parentId = id;
  }
  return { chainId, created: true };
}

// Short stage names for sentences ("busy with your-name's extraction").
export const SHORT = { extract: "extraction", analyze: "analyze", derive: "web images", import: "import", scrub: "file removal", discard: "extra-frame discard", palette: "palette regeneration" };

// Why a chain exists, from its root stage's params. `purpose` is set by
// the queueing buttons since 2026-09-30; chains from before carry only
// the flag, and an import-only chain's flag is read before an inherited
// `rerun` (which a re-run's import also carries).
function purposeOf(root) {
  const p = root.params || {};
  if (p.purpose === "repalette") return "Describe the palettes again";
  if (p.purpose === "reread") return "Read the run records again";
  if (p.rerun) return "Re-run";
  return null;
}

// Stages that are not part of an onboarding: the disk half of a removal
// (removal.js), one per removed work, run by the source worker.
const OTHER_LABELS = { scrub: "Delete its files from disk (removed)", discard: "Delete the extra frames (review done)", palette: "Describe the palettes again", list: "List a folder's files" };
const OTHER_STAGES = Object.keys(OTHER_LABELS);
// A folder listing for the "add a folder" page (local.js) is a job row
// under a work id that is not a work; the page that asked for it is where
// it is watched, so the Jobs page leaves it out everywhere.
export const LISTING_STAGE = "list";
// A discard that is queued or has run at all: the work's extra frames may
// be gone, so it cannot be re-run (the picker could choose a missing frame).
const discardedWork = (r) => r.stage === "discard" && (r.state !== "cancelled" || r.attempt > 0);

const HOLDING = ["leased", "running", "cancel_requested"];

// Every chain, newest first, with what each stage is waiting for.
export async function listChains() {
  return (await jobsOverview()).chains;
}

// The Jobs page's view: every chain, plus each machine's line. Each worker
// runs one stage at a time and never interrupts one, taking the oldest
// runnable stage of its kind when it is free (the workers' lease query:
// parent committed, not backing off, oldest first). So a stage that is
// ready still waits for whatever its machine is running; the page says so
// instead of listing all waiting work as one queue.
export async function jobsOverview() {
  const rows = (await db().select().from(jobs).orderBy(desc(jobs.createdAt), asc(jobs.id))).filter((r) => r.stage !== LISTING_STAGE);
  const seen = await db().select().from(workers);
  const usual = await usualRates();
  // A work's episode title as it reads now (a typed correction first), so
  // the Jobs search finds a row by the title the gallery shows; a chain
  // whose work is not in the gallery keeps the title it was queued with.
  const titled = new Map((await db().execute(sql`select id, entry_type, coalesce(episode_title_human, episode_title) as episode_title from works`)).rows.map((w) => [w.id, w]));
  // The usual range, in minutes, for a stage of this kind on a video this
  // long; null without enough history or a known length.
  const usualFor = (s) => {
    const film = s.params?.identity?.entry_type === "movie";
    const rate = usual.get(`${s.stage}|${film}`);
    const minutes = Number(s.params?.source?.media?.duration_seconds) / 60;
    if (!rate || !(minutes > 0)) return null;
    const toMin = (perMinute) => Math.max(1, Math.round((perMinute * minutes) / 60));
    return { low: toMin(rate.low), high: toMin(rate.high), film };
  };
  const live = new Set();
  const now = Date.now();
  for (const w of seen) {
    if (now - new Date(w.lastSeenAt).getTime() < LIVE_MS) for (const c of w.capabilities || []) live.add(c);
  }
  live.add("gallery");
  const byId = new Map(rows.map((r) => [r.id, r]));
  const chains = new Map();
  for (const r of rows) {
    if (!chains.has(r.chainId)) chains.set(r.chainId, []);
    chains.get(r.chainId).push(r);
  }
  const order = new Map([...STAGES.map((s, i) => [s.stage, i]), ...OTHER_STAGES.map((st) => [st, STAGES.length])]);
  // Each machine's line: what it holds now, and its runnable stages in the
  // order it will take them.
  const lines = new Map();
  const lineOf = (cap) => {
    if (!lines.has(cap)) lines.set(cap, { busy: [], ready: [], later: [] });
    return lines.get(cap);
  };
  for (const r of rows) {
    if (HOLDING.includes(r.state)) {
      lineOf(r.capability).busy.push({ workId: r.workId, stage: SHORT[r.stage] ?? r.stage, owner: r.owner, startedAt: r.startedAt });
      continue;
    }
    if (r.state !== "queued" && r.state !== "retry_wait") continue;
    const parent = r.parentId ? byId.get(r.parentId) : null;
    if (parent && parent.state !== "committed") continue;
    const at = r.notBefore ? new Date(r.notBefore).getTime() : 0;
    (at > now ? lineOf(r.capability).later : lineOf(r.capability).ready).push(r);
  }
  const byAge = (a, b) => new Date(a.createdAt) - new Date(b.createdAt) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const place = new Map();
  for (const line of lines.values()) {
    line.ready.sort(byAge).forEach((r, i) => place.set(r.id, { position: i + 1, ready: line.ready.length }));
    for (const r of line.later) place.set(r.id, { retryAt: r.notBefore });
  }
  const machines = [...new Set([...STAGES.map((s) => s.capability), ...lines.keys()])].map((cap) => ({
    capability: cap,
    label: MACHINE[cap] ?? cap,
    live: live.has(cap),
    busy: lines.get(cap)?.busy ?? [],
    ready: lines.get(cap)?.ready.length ?? 0,
  }));
  // A work can be re-run from its newest chain once nothing of it is open.
  // "Newest" counts chains that committed something: a chain that never
  // ran (a cancelled re-run, #44) is newer than the finished chain it was
  // meant to replace and must not shadow it, or the work loses its Re-run
  // button and "Re-run every finished work" skips it.
  const newest = new Map();
  const openWork = new Set();
  const discarded = new Set();
  for (const r of rows) {
    if (OPEN.includes(r.state)) openWork.add(r.workId);
    if (discardedWork(r)) discarded.add(r.workId);
    if (r.state !== "committed") continue;
    const t = new Date(r.createdAt).getTime();
    if (!newest.has(r.workId) || t > newest.get(r.workId).t) newest.set(r.workId, { t, chainId: r.chainId });
  }
  // A chain's stages in the order they run: from the root (no parent)
  // down the parent links. The fixed stage table was wrong for any chain
  // that is not an onboarding (a palette regeneration runs palette, then
  // import; the table put import first, so the row showed the wrong half).
  const inRunOrder = (stages) => {
    const byParent = new Map(stages.map((s) => [s.parentId ?? null, s]));
    const out = [];
    let cur = byParent.get(null) ?? stages.find((s) => !stages.some((o) => o.id === s.parentId)) ?? stages[0];
    const seen = new Set();
    while (cur && !seen.has(cur.id)) {
      out.push(cur);
      seen.add(cur.id);
      cur = stages.find((s) => s.parentId === cur.id);
    }
    for (const s of stages) if (!seen.has(s.id)) out.push(s);
    return out;
  };
  const list = [...chains.entries()].map(([chainId, stagesIn]) => {
    const stages = inRunOrder(stagesIn);
    const root = stages[0];
    const stuck = stages.find((s) => ["dead_letter", "blocked", "ineligible"].includes(s.state));
    const running = stages.find((s) => ["leased", "running", "cancel_requested"].includes(s.state));
    const done = stages.every((s) => s.state === "committed");
    const cancelled = !done && !stuck && stages.some((s) => s.state === "cancelled") && !stages.some((s) => OPEN.includes(s.state));
    const current = stuck ?? running ?? stages.find((s) => s.state !== "committed" && s.state !== "cancelled") ?? null;
    const analyze = stages.find((s) => s.stage === "analyze");
    return {
      chainId,
      rerun: Boolean(root.params?.rerun),
      // Why the chain exists, when it is not a plain onboarding: shown on
      // the row, since the stage names alone ("Add them to the gallery")
      // say nothing about which button queued it.
      purpose: purposeOf(root),
      budget: analyze?.params?.budget?.choice ?? "balanced",
      removal: root.stage === "scrub" ? root.params?.removal ?? {} : null,
      discard: root.stage === "discard" ? root.result ?? {} : null,
      rerunnable: !OTHER_STAGES.includes(root.stage) && done && newest.get(root.workId)?.chainId === chainId && !openWork.has(root.workId) && !discarded.has(root.workId),
      group: stuck ? "attention" : running ? "running" : done ? "done" : cancelled ? "cancelled" : "waiting",
      current: current ? current.id : null,
      // For a waiting chain: which machine's line its next stage is in, and where.
      line: current ? { capability: current.capability, ...(place.get(current.id) ?? {}) } : null,
      finishedAt: done ? stages[stages.length - 1].finishedAt : null,
      workId: root.workId,
      title: root.params?.identity?.series_title ?? root.workId,
      episode: root.params?.identity?.episode ?? null,
      episodeTitle: titled.has(root.workId)
        ? shownEpisodeTitle(titled.get(root.workId).entry_type, titled.get(root.workId).episode_title)
        : shownEpisodeTitle(root.params?.identity?.entry_type, root.params?.identity?.episode_title),
      // The title it was queued with, searchable too: a typed correction
      // must not make the row unfindable by the name Shoko gave it.
      episodeTitleQueued: shownEpisodeTitle(root.params?.identity?.entry_type, root.params?.identity?.episode_title),
      createdAt: root.createdAt,
      open: stages.some((s) => OPEN.includes(s.state)),
      // A removal's file deletion or an extra-frame discard is not
      // cancellable: stopping one part-way leaves a work half deleted with
      // no way on (retry it if it fails).
      cancellable: !OTHER_STAGES.includes(root.stage) && stages.some((s) => OPEN.includes(s.state)),
      stages: stages.map((s) => ({
        id: s.id,
        stage: s.stage,
        label: STAGES[order.get(s.stage)]?.label ?? OTHER_LABELS[s.stage] ?? s.stage,
        machine: MACHINE[s.capability] ?? s.capability,
        state: s.state,
        waiting: waitingFor(s, byId, live),
        attempt: s.attempt,
        maxAttempts: s.maxAttempts,
        retryable: RETRYABLE.includes(s.state),
        startedAt: s.startedAt,
        usual: HOLDING.includes(s.state) ? usualFor(s) : null,
        owner: s.owner,
        errorClass: s.errorClass,
        errorMessage: s.errorMessage,
        updatedAt: s.updatedAt,
      })),
    };
  });
  return { chains: list, machines };
}

// How long each stage usually takes, in machine seconds per minute of
// video, from this gallery's own first-try stages: the 10th and 90th
// percentile, films and episodes apart (a film, 4K above all, runs several
// times slower per minute than an episode). The range is wide on purpose:
// it covers every machine that has run the stage (a GPU worker and a CPU
// one differ about threefold on analyze). Nothing for a kind
// of stage with fewer than five runs behind it.
async function usualRates() {
  const perMinute = sql`extract(epoch from finished_at - started_at) / ((params -> 'source' -> 'media' ->> 'duration_seconds')::float / 60)`;
  const r = await db().execute(sql`select stage, (coalesce(params -> 'identity' ->> 'entry_type', '') = 'movie') as film,
      percentile_cont(0.1) within group (order by ${perMinute}) as low,
      percentile_cont(0.9) within group (order by ${perMinute}) as high
    from jobs
    where stage in ('extract', 'analyze', 'derive')
      and state = 'committed' and attempt = 1 and started_at is not null and finished_at is not null
      and (params -> 'source' -> 'media' ->> 'duration_seconds')::float > 0
    group by 1, 2 having count(*) >= 5`);
  return new Map(r.rows.map((x) => [`${x.stage}|${x.film}`, { low: Number(x.low), high: Number(x.high) }]));
}

function waitingFor(job, byId, live) {
  if (job.state !== "queued" && job.state !== "retry_wait") return null;
  const parent = job.parentId ? byId.get(job.parentId) : null;
  if (parent && parent.state !== "committed") return `for "${STAGES.find((s) => s.stage === parent.stage)?.label ?? OTHER_LABELS[parent.stage] ?? parent.stage}" to finish`;
  if (!live.has(job.capability)) return `for the ${MACHINE[job.capability] ?? job.capability} (not checked in)`;
  if (job.state === "retry_wait") return "to retry";
  return "for a worker to pick it up";
}

// Stops a chain: stages nobody holds are cancelled at once; a stage a
// worker holds is asked to stop and the worker confirms (it may be mid-way
// through an ffmpeg run). Finished stages keep their state and output.
export async function cancelChain(chainId) {
  await db()
    .update(jobs)
    .set({
      state: sql`case when ${jobs.state} in ('leased', 'running') then 'cancel_requested' else 'cancelled' end`,
      updatedAt: sql`now()`,
    })
    .where(sql`${jobs.chainId} = ${chainId} and ${jobs.stage} not in ('scrub', 'discard') and ${jobs.state} in ('queued', 'retry_wait', 'blocked', 'leased', 'running')`);
}

// Stages the operator can send round again (research/04 "one-click retry
// for idempotent stages"): one that gave up, was blocked or refused, or is
// sitting out its back-off. It runs as soon as a worker is free, with one
// more attempt than it has used, so the attempts already spent stay on the
// record. Its last error stays visible until the next attempt ends.
export const RETRYABLE = ["dead_letter", "blocked", "ineligible", "retry_wait"];

// A work whose extra frames were discarded (or whose discard is queued or
// has run) cannot run analyze, derive or import again: the picker and the
// web images would need frames that are gone. Same rule as rerunnable.
const NEEDS_SURPLUS = ["analyze", "derive", "import"];

// Returns "ok", "gone" (nothing to retry), "discarded" or "taken".
export async function retryStage(jobId) {
  return db().transaction(async (tx) => {
    await tx.execute(OPERATOR_LOCK);
    const j = (await tx.execute(sql`select id, work_id, chain_id, stage, state from jobs where id = ${jobId} for update`)).rows[0];
    if (!j || !RETRYABLE.includes(j.state)) return "gone";
    // An extraction that gave up freed its work id (workIdTaken). If
    // another onboarding or a work has taken the id since, reviving this
    // one would put two extractions in one run folder.
    if (j.stage === "extract" && j.state === "dead_letter") {
      const other = await tx.execute(sql`select 1 from jobs where work_id = ${j.work_id} and stage = 'extract'
          and chain_id <> ${j.chain_id} and state not in ${RETRYABLE_END}
        union all select 1 from works where id = ${j.work_id} limit 1`);
      if (other.rows.length) return "taken";
    }
    if (NEEDS_SURPLUS.includes(j.stage)) {
      const d = await tx.execute(sql`select 1 from jobs where work_id = ${j.work_id} and stage = 'discard'
        and (state <> 'cancelled' or attempt > 0) limit 1`);
      if (d.rows.length) return "discarded";
    }
    await tx.execute(sql`update jobs set state = 'retry_wait', not_before = now(),
        max_attempts = greatest(max_attempts, attempt + 1), owner = null, lease_expires_at = null,
        finished_at = null, updated_at = now()
      where id = ${jobId}`);
    return "ok";
  });
}

// Re-runs a finished work from the analyze stage (research/01 "Re-run one
// stage with a newer model/configuration"): a new chain of analyze, derive
// and import hung off the work's committed extract, so the extraction is
// reused and no earlier row or record is overwritten (the old analyze record
// is moved aside, the old images are swapped out whole, review state stays).
// `budget` is fewer / balanced / more, or null to keep the work's current
// one. Returns the new chain id, or null if the work has something open,
// no committed extract, or its extra frames were discarded after review
// (a re-run could pick a frame that is gone; ADR-0009).
export async function rerunFromAnalyze(workId, budget, requestedBy) {
  return db().transaction(async (tx) => {
    await tx.execute(OPERATOR_LOCK);
    const ex = await tx.execute(sql`select * from jobs where work_id = ${workId} and stage = 'extract'
      and state = 'committed' order by finished_at desc limit 1 for update`);
    const extract = ex.rows[0];
    if (!extract) return null;
    const gone = await tx.execute(sql`select 1 from jobs where work_id = ${workId} and stage = 'discard'
      and (state <> 'cancelled' or attempt > 0) limit 1`);
    if (gone.rows.length) return null;
    const open = await tx.execute(sql`select 1 from jobs where work_id = ${workId}
      and state = any(${`{${OPEN.join(",")}}`}::text[]) limit 1`);
    if (open.rows.length) return null;
    const last = await tx.execute(sql`select params, chain_id from jobs where work_id = ${workId} and stage = 'analyze'
      and state = 'committed' order by finished_at desc limit 1`);
    // Locked stills are the operator's pins (ADR-0008): the picker keeps
    // them in the set whatever else it chooses, which is also how a still a
    // re-run dropped comes back (lock it, re-run).
    const locked = await tx.execute(sql`select candidate_id from jobs j, stills s
      where j.id = ${extract.id} and s.work_id = ${workId} and s.locked order by s.candidate_id`);
    const params = {
      ...extract.params,
      budget: budget ?? last.rows[0]?.params?.budget ?? extract.params?.budget ?? null,
      pins: locked.rows.map((r) => r.candidate_id),
      rerun: { from: "analyze", of: last.rows[0]?.chain_id ?? extract.chain_id, at: new Date().toISOString() },
    };
    const chainId = randomUUID();
    let parentId = extract.id;
    for (const [i, s] of STAGES.slice(1).entries()) {
      const id = i === 0 ? chainId : randomUUID();
      await tx.insert(jobs).values({
        id, chainId, parentId, stage: s.stage, capability: s.capability,
        idempotencyKey: `${s.stage}:rerun:${chainId}`, workId, params, requestedBy,
      });
      parentId = id;
    }
    return chainId;
  });
}

// Palettes again for every work, after a palette-descriptor update: a
// `palette` stage for the source worker (it describes every still from its
// master in the work's run records), then an `import`. Works with an open
// stage are skipped.
export async function repaletteAll(requestedBy) {
  return db().transaction(async (tx) => {
    const r = await tx.execute(sql`select w.id as work_id,
        (select j.params from jobs j where j.work_id = w.id and j.stage = 'import' and j.state = 'committed'
          order by j.finished_at desc nulls last limit 1) as params
      from works w
      where exists (select 1 from jobs e where e.work_id = w.id and e.stage = 'extract' and e.state = 'committed')
        and not exists (select 1 from jobs o where o.work_id = w.id
        and o.state in ('queued', 'retry_wait', 'leased', 'running', 'cancel_requested'))
      order by w.id`);
    let chains = 0;
    for (const row of r.rows) {
      const chainId = randomUUID();
      const { repalette: _a, reread: _b, rerun: _c, purpose: _d, ...inherited } = row.params || {};
      const params = { ...inherited, purpose: "repalette", repalette: { at: new Date().toISOString() } };
      await tx.insert(jobs).values({
        id: chainId, chainId, parentId: null, stage: "palette", capability: "source",
        idempotencyKey: `palette:${chainId}`, workId: row.work_id, params, requestedBy,
      });
      await tx.insert(jobs).values({
        id: randomUUID(), chainId, parentId: chainId, stage: "import", capability: "gallery",
        idempotencyKey: `import:palette:${chainId}`, workId: row.work_id, params, requestedBy,
      });
      chains += 1;
    }
    return chains;
  });
}

// One import stage per work whose run records are on disk (a committed
// import says so), for the gallery's own worker: import is idempotent and
// leaves review marks, locks, exclusions and corrections alone, so this
// only refreshes the pipeline columns (added 2026-09-30 for the reasons to
// look). Works with an open stage, or being removed, are skipped.
export async function reimportAll(requestedBy) {
  return db().transaction(async (tx) => {
    const r = await tx.execute(sql`select distinct on (j.work_id) j.work_id, j.params from jobs j
      where j.stage = 'import' and j.state = 'committed'
        and j.work_id in (select id from works)
        and not exists (select 1 from jobs o where o.work_id = j.work_id
          and o.state in ('queued', 'retry_wait', 'leased', 'running', 'cancel_requested'))
      order by j.work_id, j.finished_at desc nulls last`);
    let n = 0;
    for (const row of r.rows) {
      const chainId = randomUUID();
      await tx.insert(jobs).values({
        id: chainId, chainId, parentId: null, stage: "import", capability: "gallery",
        idempotencyKey: `import:reread:${chainId}`, workId: row.work_id,
        params: (({ repalette: _a, reread: _b, rerun: _c, purpose: _d, ...rest }) => ({ ...rest, purpose: "reread", reread: { at: new Date().toISOString() } }))(row.params || {}),
        requestedBy,
      });
      n += 1;
    }
    return n;
  });
}

// Cancels exactly the chains the Jobs page lists under Waiting: nothing
// held by a worker, nothing that needs you (blocked, refused, given up),
// nothing already done. A removal's file deletion and a discard are not
// onboardings and are left alone.
// chainIds: exactly the chains the page listed (a search narrows the
// list, and the button must not reach past what it showed).
export async function cancelWaiting(chainIds) {
  const ids = [...new Set((chainIds || []).map(String))].filter((id) => /^[0-9a-f-]{36}$/.test(id));
  if (!ids.length) return 0;
  const r = await db().execute(sql`update jobs set state = 'cancelled', updated_at = now()
    where state in ('queued', 'retry_wait') and stage not in ('scrub', 'discard')
      and chain_id in ${ids}
      and chain_id not in (select chain_id from jobs
        where state in ('leased', 'running', 'cancel_requested', 'blocked', 'dead_letter', 'ineligible'))
    returning chain_id`);
  return new Set(r.rows.map((x) => x.chain_id)).size;
}

// What a batch will cost, from this gallery's own history: machine seconds
// per minute of video for each stage, and stills picked per minute, over
// first-attempt stages that committed. Null until something has run.
export async function estimates() {
  const r = await db().execute(sql`select stage, count(*)::int as n,
      sum(extract(epoch from finished_at - started_at))::float as secs,
      sum((params -> 'source' -> 'media' ->> 'duration_seconds')::float)::float as video,
      sum((result ->> 'selected')::float)::float as selected
    from jobs
    where state = 'committed' and attempt = 1 and started_at is not null and finished_at is not null
      and (params -> 'source' -> 'media' ->> 'duration_seconds')::float > 0
    group by stage`);
  const by = Object.fromEntries(r.rows.map((x) => [x.stage, x]));
  if (!by.extract || !by.analyze) return null;
  const perMinute = (x) => (x ? x.secs / (x.video / 60) : 0);
  return {
    runs: by.analyze.n,
    secondsPerMinute: STAGES.reduce((t, s) => t + perMinute(by[s.stage]), 0),
    stillsPerMinute: by.analyze.selected / (by.analyze.video / 60),
  };
}

// Which of these Shoko files have a live chain (open or committed); a
// cancelled or abandoned one does not count, so the file can start over.
export async function chainsForFiles(fileIds) {
  if (!fileIds.length) return new Map();
  const rows = await db()
    .select({ fileId: sql`(${jobs.params} -> 'identity' ->> 'file_id')::int`, state: jobs.state, chainId: jobs.chainId })
    .from(jobs)
    .where(sql`${jobs.stage} = 'extract' and ${jobs.state} not in ${RETRYABLE_END} and (${jobs.params} -> 'identity' ->> 'file_id')::int in ${fileIds}`);
  return new Map(rows.map((r) => [r.fileId, r]));
}

// A work id belongs to a work in the gallery or to a live chain; a
// cancelled chain gives its id back.
export async function workIdTaken(workId, q = db()) {
  const [j] = await q
    .select({ id: jobs.id })
    .from(jobs)
    .where(sql`${jobs.workId} = ${workId} and ${jobs.stage} = 'extract' and ${jobs.state} not in ${RETRYABLE_END}`)
    .limit(1);
  if (j) return true;
  const [w] = await q.select({ id: schema.works.id }).from(schema.works).where(eq(schema.works.id, workId)).limit(1);
  return Boolean(w);
}

// ---- The worker side, for the gallery's own import stage and for the
// analyze workers, which reach the table only through the gallery's
// worker API (decided 2026-09-28: no database credential on an analyze
// machine). Same semantics as the source worker (worker/hikari_worker/
// jobs.py): lease the oldest runnable stage of our capability, keep it
// with heartbeats, end it committed / retried / blocked / ineligible /
// cancelled / dead. A cancel that lands while a stage runs wins.

const LEASE = "10 minutes";
const RETRY_BACKOFF_MINUTES = 15;

export async function checkIn(workerId, capabilities, version = null, standby = false) {
  await db().execute(sql`insert into workers (id, capabilities, version, standby, last_seen_at)
    values (${workerId}, ${JSON.stringify(capabilities)}::jsonb, ${version}, ${standby}, now())
    on conflict (id) do update set capabilities = excluded.capabilities, version = excluded.version,
      standby = excluded.standby, last_seen_at = now()`);
}

// For a standby worker: the regular worker of this kind that has checked
// in (a poll or a heartbeat) within the live window, if any. While one is
// on, the standby takes nothing; within LIVE_MS of it going quiet, the
// standby starts taking stages. A stage the standby already holds runs to
// the end even if the regular worker comes back.
export async function regularWorkerOn(workerId, capability) {
  const r = await db().execute(sql`select id from workers where id <> ${workerId} and not standby
      and capabilities ? ${capability} and last_seen_at > now() - make_interval(secs => ${LIVE_MS / 1000})
    order by last_seen_at desc limit 1`);
  return r.rows[0]?.id ?? null;
}

export async function releaseOwn(workerId) {
  const r = await db().execute(sql`update jobs set
      state = case when state = 'cancel_requested' then 'cancelled' else 'retry_wait' end,
      not_before = now(), owner = null, lease_expires_at = null, updated_at = now(),
      error_class = case when state = 'cancel_requested' then error_class else 'transient' end,
      error_message = case when state = 'cancel_requested' then error_message else 'the worker stopped while this stage ran' end
    where owner = ${workerId} and state in ('leased', 'running', 'cancel_requested') returning id`);
  return r.rows.length;
}

export async function leaseFor(workerId, capabilities) {
  return db().transaction(async (tx) => {
    const found = await tx.execute(sql`select j.* from jobs j left join jobs p on p.id = j.parent_id
      where j.capability = any(${`{${capabilities.join(",")}}`}::text[])
        and ((j.state in ('queued', 'retry_wait') and (j.not_before is null or j.not_before <= now()))
             or (j.state in ('leased', 'running') and j.lease_expires_at < now()))
        and (j.parent_id is null or p.state = 'committed')
      order by j.created_at, j.id for update of j skip locked limit 1`);
    const job = found.rows[0];
    if (!job) return null;
    if (job.attempt >= job.max_attempts) {
      await tx.execute(sql`update jobs set state = 'dead_letter', owner = null, lease_expires_at = null,
        finished_at = now(), updated_at = now() where id = ${job.id}`);
      return null;
    }
    // started_at is when THIS try began: Jobs shows it as the running time,
    // and a retry hours later must not read as hours of work.
    const leased = await tx.execute(sql`update jobs set state = 'running', owner = ${workerId}, attempt = attempt + 1,
        lease_expires_at = now() + ${LEASE}::interval, heartbeat_at = now(),
        started_at = now(), updated_at = now(), not_before = null
      where id = ${job.id} returning *`);
    return leased.rows[0];
  });
}

// Extends the lease; returns the stage's state, or null if it is not ours.
export async function beat(jobId, workerId) {
  const r = await db().execute(sql`update jobs set heartbeat_at = now(),
      lease_expires_at = now() + ${LEASE}::interval, updated_at = now()
    where id = ${jobId} and owner = ${workerId} returning state`);
  // A busy worker is still a live one on the Jobs page.
  await db().execute(sql`update workers set last_seen_at = now() where id = ${workerId}`);
  return r.rows[0]?.state ?? null;
}

export async function jobById(jobId) {
  const r = await db().execute(sql`select * from jobs where id = ${jobId}`);
  return r.rows[0] ?? null;
}

export async function parentChain(job) {
  const out = [];
  let parent = job.parent_id;
  while (parent) {
    const p = await jobById(parent);
    if (!p) break;
    out.push(p);
    parent = p.parent_id;
  }
  return out;
}

// Returns the final state: committed, or cancelled when a cancel was
// requested while it ran (the output stays on the record).
export async function commitJob(jobId, workerId, result) {
  const r = await db().execute(sql`update jobs set
      state = case when state = 'cancel_requested' then 'cancelled' else 'committed' end,
      result = ${JSON.stringify(result)}::jsonb, owner = null, lease_expires_at = null,
      error_class = case when state = 'cancel_requested' then 'cancelled' else null end,
      error_message = case when state = 'cancel_requested' then 'cancelled while it ran; its output is kept' else null end,
      finished_at = now(), updated_at = now()
    where id = ${jobId} and owner = ${workerId} returning state`);
  return r.rows[0]?.state ?? "lost";
}

export async function failJob(job, workerId, errorClass, message, { retryNow = false } = {}) {
  const msg = String(message ?? "").slice(-2000);
  const state =
    errorClass === "cancelled" ? "cancelled"
    : errorClass === "input" ? "ineligible"
    : errorClass === "source" ? "blocked"
    : job.attempt >= job.max_attempts ? "dead_letter"
    : "retry_wait";
  const backoff = retryNow ? 0 : RETRY_BACKOFF_MINUTES * Math.max(1, job.attempt);
  await db().execute(sql`update jobs set state = ${state}, owner = null, lease_expires_at = null,
      error_class = ${errorClass}, error_message = ${msg},
      not_before = case when ${state} = 'retry_wait' then now() + make_interval(mins => ${backoff}) else null end,
      finished_at = case when ${state} = 'retry_wait' then null else now() end, updated_at = now()
    where id = ${job.id} and owner = ${workerId}`);
  return state;
}
