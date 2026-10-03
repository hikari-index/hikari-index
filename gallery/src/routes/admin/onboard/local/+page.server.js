import { fail } from "@sveltejs/kit";
import { MACHINE, POLICY, STAGES } from "$lib/server/jobs.js";
import { cleanLine, cleanPath, localCatalogue, localIdentity, localSource, queueLocal, sourceRoots } from "$lib/server/local.js";
import { BUDGETS, defaultWorkId, WORK_ID } from "$lib/server/onboard.js";
import { shokoConfigured } from "$lib/server/shoko.js";

// Add a work from a local file, without Shoko (ADR-0014): one file per
// submit, described by hand. research/01's rule holds: nothing runs until
// the operator confirms, and nothing is scanned.

export async function load() {
  return {
    roots: await sourceRoots(),
    // With Shoko here, a folder named by a number is one of Shoko's.
    shoko: shokoConfigured(),
    catalogue: await localCatalogue(),
    budgets: BUDGETS,
    stages: STAGES.map((s) => ({ label: s.label, machine: `the ${MACHINE[s.capability]}` })),
  };
}

const NOTHING = "Nothing was queued; fix the fields marked below.";

export const actions = {
  default: async ({ request, locals }) => {
    const form = await request.formData();
    const text = (name) => String(form.get(name) ?? "");
    const values = {
      root: text("root").trim(),
      path: text("path").trim(),
      kind: text("kind") === "movie" ? "movie" : "episode",
      title: text("title"),
      episode: text("episode").trim(),
      episode_title: text("episode_title"),
      group: text("group"),
      work_id: text("work_id").trim().toLowerCase(),
      budget: text("budget") || "balanced",
    };
    const problems = {};
    // Only a folder a worker has reported: a worker from before this page
    // reports none, would take the job anyway, and could only block it.
    const roots = await sourceRoots();
    if (!roots.names.length) return fail(409, { values, problems, message: "Nothing was queued: no worker has reported a source folder yet." });
    if (!roots.names.includes(values.root)) problems.root = "Choose the source folder the file is in.";
    const path = cleanPath(values.path);
    if (!path) problems.path = "Give the file's path inside the source folder, without “..” parts.";
    const title = cleanLine(values.title);
    if (!title) problems.title = "Give the title, 200 characters at most.";
    const group = cleanLine(values.group);
    if (group == null) problems.group = "200 characters at most.";
    const episodeTitle = cleanLine(values.episode_title);
    if (episodeTitle == null) problems.episode_title = "200 characters at most.";
    const episode = /^\d{1,4}$/.test(values.episode) ? Number(values.episode) : null;
    if (values.kind === "episode" && episode == null) problems.episode = "Give the episode's number (0 to 9999).";
    if (!Object.hasOwn(BUDGETS, values.budget)) problems.budget = "Choose fewer, balanced or more.";
    if (Object.keys(problems).length) return fail(400, { values, problems, message: NOTHING });

    const workId = values.work_id || defaultWorkId(title, values.kind, episode);
    const shown = { ...values, work_id: workId };
    if (!WORK_ID.test(workId)) return fail(400, { values: shown, problems: { work_id: "The work id takes lowercase letters, digits and dashes, 2 to 72 characters." }, message: NOTHING });

    const r = await queueLocal({
      root: values.root,
      path,
      workId,
      seriesTitle: title,
      groupTitle: group,
      requestedBy: locals.admin?.user ?? null,
      describe: (placed) => ({
        identity: localIdentity({ seriesTitle: placed.seriesTitle, seriesId: placed.seriesId, fileId: placed.fileId, entryType: values.kind, episode, episodeTitle, path, workId }),
        source: localSource(values.root, path),
        policy: POLICY,
        budget: { choice: values.budget, factor: BUDGETS[values.budget].factor },
        local: { title: placed.seriesTitle, group: group || null },
      }),
    });
    if (r.removing) return fail(409, { values: shown, problems: { work_id: `“${workId}” is still being removed: its old files are waiting for the worker to delete them (Jobs). Add it again once that finishes.` }, message: NOTHING });
    if (r.taken) return fail(409, { values: shown, problems: { work_id: `“${workId}” is already used by another work; type another.` }, message: NOTHING });
    if (r.existing) {
      return fail(409, {
        values: shown,
        problems: { path: `This file is already queued or in the gallery as “${r.existing.work_id}”. To start it over: cancel it in Jobs if its first step has not finished, or remove the work (Remove…, on its row) if it has; then add it again.` },
        existing: r.existing.chain_id,
        message: NOTHING,
      });
    }
    // The form comes back ready for the next file of the same series: the
    // same folder, title and still count, the next episode number, the path
    // left to be edited (a season's files usually differ by a few characters).
    const next = values.kind === "episode"
      ? { ...values, title: r.placed.seriesTitle, episode: String(Math.min(9999, episode + 1)), episode_title: "", work_id: "" }
      : { ...values, path: "", title: "", work_id: "" };
    return {
      values: next,
      queued: { workId, chainId: r.chainId, label: values.kind === "movie" ? r.placed.seriesTitle : `${r.placed.seriesTitle} · episode ${episode}` },
    };
  },
};
