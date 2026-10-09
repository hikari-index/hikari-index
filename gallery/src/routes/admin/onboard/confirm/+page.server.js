import { error, fail, redirect } from "@sveltejs/kit";
import { chainsForFiles, enqueueOnboarding, estimates, MACHINE, POLICY, STAGES, workIdTaken } from "$lib/server/jobs.js";
import { beingRemoved } from "$lib/server/removal.js";
import { BATCH_MAX, BUDGETS, defaultWorkId, fileProblems, identityFor, sourceFor, WORK_ID } from "$lib/server/onboard.js";
import { shoko, ShokoError } from "$lib/server/shoko.js";

// The confirm page, for one file or a batch. Everything is re-read from
// Shoko here and again on submit, so what is queued is what Shoko says now,
// not what the series page saw. research/01: "an authenticated operator
// chooses a source edition, reviews the budget and exclusion policy, and
// explicitly confirms"; research/03: "Select one or several items" and
// "Preview cost/still-budget estimate before enqueueing".

function fileIds(values) {
  const ids = [...new Set(values.map(Number))];
  if (!ids.length || ids.some((id) => !Number.isInteger(id) || id <= 0)) error(400, "no files chosen");
  if (ids.length > BATCH_MAX) error(400, `at most ${BATCH_MAX} files at once`);
  return ids;
}

async function readOne(c, fileId) {
  const detail = await c.file(fileId);
  const { blockers, notes } = fileProblems(detail, detail);
  if (blockers.length) return { fileId, detail, blockers, notes };
  const xref = detail.SeriesIDs[0];
  const [series, episode] = await Promise.all([c.series(xref.SeriesID.ID), c.episode(xref.EpisodeIDs[0].ID)]);
  return { fileId, detail, series, episode, blockers, notes };
}

const TWO = "Two files for the same episode are chosen; go back and keep one.";

// A few Shoko reads at a time; a batch is a season, not the library.
async function readAll(ids) {
  const c = await shoko();
  const out = new Array(ids.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(4, ids.length) }, async () => {
      while (next < ids.length) {
        const i = next++;
        out[i] = await readOne(c, ids[i]);
      }
    }),
  );
  // Two releases of the same episode in one batch is a choice, not a batch.
  const byEpisode = new Map();
  for (const r of out) for (const e of r.detail.SeriesIDs?.[0]?.EpisodeIDs ?? []) byEpisode.set(e.ID, [...(byEpisode.get(e.ID) ?? []), r]);
  for (const rows of byEpisode.values()) {
    if (rows.length > 1) for (const r of rows) if (!r.blockers.includes(TWO)) r.blockers.push(TWO);
  }
  return out;
}

function rowOf(r, existing) {
  const entryType = r.series?.AniDB?.Type === "Movie" ? "movie" : "episode";
  const workId = r.series ? defaultWorkId(r.series.Name, entryType, r.episode?.AniDB?.EpisodeNumber, r.episode?.Name) : null;
  const where = (r.detail.Locations || [])[0]?.RelativePath ?? "";
  return {
    fileId: r.fileId,
    fileName: String(where).split(/[\\/]/).pop() || null,
    blockers: r.blockers,
    notes: r.notes,
    existing: existing.get(r.fileId) ?? null,
    workId,
    identity: r.series ? identityFor({ series: r.series, episode: r.episode, detail: r.detail, workId }) : null,
    source: sourceFor(r.detail),
    seriesId: r.detail.SeriesIDs?.[0]?.SeriesID?.ID ?? null,
  };
}

export async function load({ url }) {
  const ids = fileIds(url.searchParams.getAll("file"));
  let read;
  try {
    read = await readAll(ids);
  } catch (e) {
    if (e instanceof ShokoError) return { error: e.message };
    throw e;
  }
  const existing = await chainsForFiles(ids);
  const rows = read.map((r) => rowOf(r, existing));
  const defaults = rows.filter((r) => r.workId).map((r) => r.workId);
  for (const r of rows) {
    r.workIdRemoving = r.workId ? await beingRemoved(r.workId) : false;
    r.workIdTaken = r.workId ? r.workIdRemoving || (await workIdTaken(r.workId)) || defaults.filter((w) => w === r.workId).length > 1 : false;
  }
  return {
    error: null,
    rows,
    back: rows.find((r) => r.seriesId)?.seriesId ?? null,
    estimate: await estimates(),
    budgets: BUDGETS,
    policy: POLICY,
    stages: STAGES.map((s) => ({ label: s.label, machine: `the ${MACHINE[s.capability]}` })),
  };
}

export const actions = {
  default: async ({ request, locals }) => {
    const form = await request.formData();
    const ids = fileIds(form.getAll("file"));
    const budget = String(form.get("budget") ?? "balanced");
    if (!Object.hasOwn(BUDGETS, budget)) return fail(400, { message: "Choose fewer, balanced or more." });
    const holdChapters = form.get("hold_chapters") === "on";
    const wanted = new Map(ids.map((id) => [id, String(form.get(`work_id-${id}`) ?? "").trim().toLowerCase()]));
    const problems = {};
    for (const [id, w] of wanted) if (!WORK_ID.test(w)) problems[id] = "The work id takes lowercase letters, digits and dashes, 2 to 72 characters.";
    const counts = new Map();
    for (const w of wanted.values()) counts.set(w, (counts.get(w) ?? 0) + 1);
    for (const [id, w] of wanted) if (!problems[id] && counts.get(w) > 1) problems[id] = `“${w}” is used twice in this batch.`;
    let read;
    try {
      read = await readAll(ids);
    } catch (e) {
      if (e instanceof ShokoError) return fail(502, { workIds: Object.fromEntries(wanted), message: e.message });
      throw e;
    }
    const existing = await chainsForFiles(ids);
    for (const r of read) {
      const id = r.fileId;
      if (problems[id]) continue;
      if (r.blockers.length) problems[id] = r.blockers.join(" ");
      else if (existing.get(id)) problems[id] = "This file was onboarded meanwhile.";
      else if (await beingRemoved(wanted.get(id))) problems[id] = `“${wanted.get(id)}” is still being removed: its old files are waiting for the source worker to delete them (Jobs). Onboard it again once that finishes.`;
      else if (await workIdTaken(wanted.get(id))) problems[id] = `“${wanted.get(id)}” is already used by another work; choose another.`;
    }
    // All or nothing: a batch that half-queued would leave the operator
    // working out which half.
    if (Object.keys(problems).length) {
      return fail(409, { workIds: Object.fromEntries(wanted), budget, problems, message: "Nothing was queued; fix the rows marked below." });
    }
    for (const r of read) {
      const workId = wanted.get(r.fileId);
      const source = sourceFor(r.detail);
      const identity = identityFor({ series: r.series, episode: r.episode, detail: r.detail, workId });
      await enqueueOnboarding({
        workId,
        fingerprint: source.fingerprint,
        size: source.size,
        params: {
          identity,
          source,
          policy: POLICY,
          budget: { choice: budget, factor: BUDGETS[budget].factor },
          hold_chapters: holdChapters,
          shoko: { series_name: r.series.Name, anidb_type: r.series.AniDB?.Type ?? null },
        },
        requestedBy: locals.admin?.user ?? null,
      });
    }
    redirect(303, `/admin/jobs?queued=${read.length}`);
  },
};
