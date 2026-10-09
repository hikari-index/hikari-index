import { error, fail, redirect } from "@sveltejs/kit";
import { sql } from "drizzle-orm";
import { db } from "$lib/server/db/index.js";
import { guessEpisode, looksLikeExtra, naturalCompare } from "$lib/filenames.js";
import { MACHINE, POLICY, STAGES } from "$lib/server/jobs.js";
import { cancelListing, cleanLine, existingLocalMany, listingById, localCatalogue, localIdentity, localSource, queueListing, queueLocalBatch } from "$lib/server/local.js";
import { BUDGETS, defaultWorkId, WORK_ID } from "$lib/server/onboard.js";

// One folder listing: while the worker has not answered, the page waits
// (and refreshes itself); once it has, the files are shown for the
// operator to tick, number and confirm, and the confirm queues one chain
// per ticked file, all or nothing (ADR-0014 amendment).

const OPEN = ["queued", "retry_wait", "leased", "running", "cancel_requested"];

// Whether a worker that opens raw source has checked in lately, and how
// long ago the newest did: the waiting page says who it waits for.
async function sourceWorker() {
  const r = await db().execute(sql`select id, version, last_seen_at,
      last_seen_at > now() - interval '10 minutes' as live
    from workers where capabilities ? 'source' order by last_seen_at desc limit 1`);
  return r.rows[0] ?? null;
}

// The files as the page shows them, in natural order, each with the guess
// and whether it is already here.
async function rowsOf(listing) {
  const files = [...(listing.result?.files ?? [])].sort((a, b) => naturalCompare(a.name, b.name));
  const pathOf = (name) => (listing.path ? `${listing.path}/${name}` : name);
  const existing = await existingLocalMany(listing.root, files.map((f) => pathOf(f.name)));
  return files.map((f, i) => ({
    i,
    name: f.name,
    path: pathOf(f.name),
    size: f.size,
    guess: guessEpisode(f.name),
    extra: looksLikeExtra(f.name),
    existing: existing.get(pathOf(f.name)) ?? null,
  }));
}

export async function load({ params }) {
  const listing = await listingById(params.id);
  if (!listing) error(404, "No such listing");
  const open = OPEN.includes(listing.state);
  return {
    listing,
    open,
    worker: open || listing.state !== "committed" ? await sourceWorker() : null,
    rows: listing.state === "committed" ? await rowsOf(listing) : [],
    catalogue: await localCatalogue(),
    budgets: BUDGETS,
    stages: STAGES.map((s) => ({ label: s.label, machine: `the ${MACHINE[s.capability]}` })),
  };
}

const NOTHING = "Nothing was queued; fix the rows marked below.";

export const actions = {
  // Confirm: the ticked files become works of one series.
  queue: async ({ request, params, locals }) => {
    const listing = await listingById(params.id);
    if (!listing || listing.state !== "committed") return fail(409, { message: "This listing is not ready to confirm." });
    const rows = await rowsOf(listing);
    const form = await request.formData();
    const text = (name) => String(form.get(name) ?? "");
    const values = {
      title: text("title"),
      group: text("group"),
      budget: text("budget") || "balanced",
      hold_chapters: form.get("hold_chapters") === "on",
      rows: Object.fromEntries(rows.map((r) => [r.i, {
        pick: form.get(`pick-${r.i}`) != null,
        episode: text(`episode-${r.i}`).trim(),
        episode_title: text(`episode_title-${r.i}`),
        work_id: text(`work_id-${r.i}`).trim().toLowerCase(),
      }])),
    };
    const problems = { rows: {} };
    const title = cleanLine(values.title);
    if (!title) problems.title = "Give the series title, 200 characters at most.";
    const group = cleanLine(values.group);
    if (group == null) problems.group = "200 characters at most.";
    if (!Object.hasOwn(BUDGETS, values.budget)) problems.budget = "Choose fewer, balanced or more.";
    const picked = [];
    for (const r of rows) {
      const v = values.rows[r.i];
      if (!v.pick) continue;
      if (r.existing) { problems.rows[r.i] = `Already queued or in the gallery as “${r.existing.work_id}”.`; continue; }
      const episode = /^\d{1,4}$/.test(v.episode) ? Number(v.episode) : null;
      if (episode == null) { problems.rows[r.i] = "Give the episode's number (0 to 9999)."; continue; }
      const episodeTitle = cleanLine(v.episode_title);
      if (episodeTitle == null) { problems.rows[r.i] = "The episode title takes 200 characters at most."; continue; }
      // With no title there is no default id to check; the title's own
      // message is enough for that case.
      const workId = v.work_id || (title ? defaultWorkId(title, "episode", episode) : "");
      if ((v.work_id || title) && !WORK_ID.test(workId)) { problems.rows[r.i] = "The work id takes lowercase letters, digits and dashes, 2 to 72 characters."; continue; }
      picked.push({ i: r.i, path: r.path, name: r.name, episode, episodeTitle, workId });
    }
    // Two files for one episode, or two rows with one id, is a choice,
    // not a batch.
    const byEpisode = new Map();
    const byId = new Map();
    for (const p of picked) {
      byEpisode.set(p.episode, (byEpisode.get(p.episode) ?? 0) + 1);
      // No title means no default id yet; an empty id is not a repeat.
      if (p.workId) byId.set(p.workId, (byId.get(p.workId) ?? 0) + 1);
    }
    for (const p of picked) {
      if (byEpisode.get(p.episode) > 1) problems.rows[p.i] = `Episode ${p.episode} is given to more than one file; keep one.`;
      else if (byId.get(p.workId) > 1) problems.rows[p.i] = `“${p.workId}” is used twice; type another.`;
    }
    const shown = { ...values, rows: Object.fromEntries(rows.map((r) => [r.i, { ...values.rows[r.i], work_id: values.rows[r.i].work_id || picked.find((p) => p.i === r.i)?.workId || "" }])) };
    if (!picked.length && !Object.keys(problems.rows).length) problems.rows[-1] = "Tick at least one file.";
    if (Object.keys(problems.rows).length || problems.title || problems.group || problems.budget) {
      return fail(400, { values: shown, problems, message: NOTHING });
    }
    // Queued in episode order, so a season runs first episode first.
    const files = picked.filter((p) => !problems.rows[p.i]).sort((a, b) => a.episode - b.episode);
    const r = await queueLocalBatch({
      root: listing.root,
      files,
      seriesTitle: title,
      groupTitle: group,
      requestedBy: locals.admin?.user ?? null,
      describe: (placed, f) => ({
        identity: localIdentity({ seriesTitle: placed.seriesTitle, seriesId: placed.seriesId, fileId: placed.fileId, entryType: "episode", episode: f.episode, episodeTitle: f.episodeTitle, path: f.path, workId: f.workId }),
        source: localSource(listing.root, f.path),
        policy: POLICY,
        budget: { choice: values.budget, factor: BUDGETS[values.budget].factor },
        hold_chapters: values.hold_chapters,
        local: { title: placed.seriesTitle, group: group || null, folder: listing.id },
      }),
    });
    if (r.problems) {
      for (const p of files) if (r.problems[p.path]) problems.rows[p.i] = r.problems[p.path];
      return fail(409, { values: shown, problems, message: NOTHING });
    }
    redirect(303, `/admin/jobs?queued=${r.chainIds.length}`);
  },
  // Ask the worker again for the same folder (the files changed, or the
  // first ask failed).
  again: async ({ params, locals }) => {
    const listing = await listingById(params.id);
    if (!listing) error(404, "No such listing");
    const id = await queueListing({ root: listing.root, path: listing.path, requestedBy: locals.admin?.user ?? null });
    redirect(303, `/admin/onboard/folder/${id}`);
  },
  cancel: async ({ params }) => {
    await cancelListing(params.id);
    redirect(303, "/admin/onboard/folder");
  },
};
