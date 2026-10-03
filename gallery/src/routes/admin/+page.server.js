import { fail } from "@sveltejs/kit";
import { exceptionCount, keepRest, nextUnreviewedAnywhere, reviewScopeWorks, reviewTree, undoKeep } from "$lib/server/catalog.js";
import { discardStates } from "$lib/server/discard.js";
import { cullRepeats, repeatsState, undoCull } from "$lib/server/repeats.js";

// Review home: the Library's grouping with review counts at every level, a
// "continue" link to the next still nobody has looked at, and bulk accept
// for an episode or a season (research/03 Workbench: "bulk accept
// high-confidence results and resolve exceptions"). The owner reviews by
// culling what should go (OP/ED, interstitials); everything else is kept in
// bulk and stays editable. A season's openings, endings and logos are
// found for them (repeats.js) and culled in one go, per season.
export async function load({ url }) {
  return {
    ...(await reviewTree()),
    next: await nextUnreviewedAnywhere(),
    worthALook: await exceptionCount(),
    // work id -> { state, bytes } of its extra-frame discard, if any
    discards: Object.fromEntries(await discardStates()),
    // works not matched against their current siblings yet, and why not
    repeats: await repeatsState(),
    order: url.searchParams.get("order") === "az" ? "az" : "attention",
  };
}

const KINDS = new Set(["work", "season"]);

async function scope(form) {
  const kind = String(form.get("kind") ?? "");
  const id = String(form.get("id") ?? "");
  if (!KINDS.has(kind) || !id) return null;
  const works = await reviewScopeWorks(kind, id);
  return works.length ? { kind, id, works, label: String(form.get("label") ?? "").slice(0, 200) } : null;
}

const stills = (n) => `${n} still${n === 1 ? "" : "s"}`;

export const actions = {
  keep: async ({ request }) => {
    const form = await request.formData();
    const s = await scope(form);
    if (!s) return fail(400, { message: "Nothing to mark: that episode or season is not in the gallery." });
    const { count, at } = await keepRest(s.works);
    return {
      message: count ? `Marked ${stills(count)} in ${s.label} as kept.` : `Nothing left to review in ${s.label}.`,
      undo: count ? { what: "keep", kind: s.kind, id: s.id, at, label: s.label, count } : null,
    };
  },
  // A season's openings, endings and logos: every unreviewed, unlocked
  // still that repeats in at least half of the season's other episodes.
  // cullRepeats itself takes nothing from a season still being matched;
  // the check here only words the refusal.
  cull: async ({ request }) => {
    const form = await request.formData();
    const s = await scope(form);
    if (!s) return fail(400, { message: "Nothing to cull: that episode or season is not in the gallery." });
    const state = await repeatsState();
    const waiting = s.works.filter((id) => state.due.includes(id));
    const broken = waiting.find((id) => state.failed[id]);
    if (broken) return fail(409, { message: `Nothing culled: ${s.label} could not be checked for repeats (${broken}: ${state.failed[broken]}).` });
    if (waiting.length) return fail(409, { message: `${s.label} is still being checked for repeats. Try again in a few minutes.` });
    const { count, at } = await cullRepeats(s.works);
    return {
      message: count ? `Culled ${stills(count)} in ${s.label} that repeat across the season.` : `No unreviewed repeats left in ${s.label}.`,
      undo: count ? { what: "cull", kind: s.kind, id: s.id, at, label: s.label, count } : null,
    };
  },
  undo: async ({ request }) => {
    const form = await request.formData();
    const s = await scope(form);
    const at = String(form.get("at") ?? "");
    if (!s || !/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?[+-]\d\d(:\d\d)?$/.test(at)) return fail(400, { message: "That undo no longer applies." });
    const n = form.get("what") === "cull" ? await undoCull(s.works, at) : await undoKeep(s.works, at);
    return { message: n ? `Undone: ${stills(n)} in ${s.label} ${n === 1 ? "is" : "are"} unreviewed again.` : `Nothing to undo in ${s.label}; those stills have changed since.` };
  },
};
