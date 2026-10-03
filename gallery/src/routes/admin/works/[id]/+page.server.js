import { error, fail } from "@sveltejs/kit";
import { keepRest, setEpisodeTitle, setEpisodeTitleShoko, setFlag, setFlagMany, setReview, setReviewMany, workMeta, workNeighbours, workSiblings, workStills } from "$lib/server/catalog.js";
import { surplusDiscarded } from "$lib/server/discard.js";
import { poolNotInGallery, setLocks } from "$lib/server/pool.js";
import { shoko, shokoConfigured, ShokoError } from "$lib/server/shoko.js";

// The workbench for one episode: every selected still in time order with
// the review controls (research/03 "Workbench": accept, cull, lock,
// exclude), one at a time or over a selection (shift-click ranges: an
// opening or ending is a run of stills). ?show=unreviewed|culled|hidden|
// repeats narrows.
export async function load({ params, url }) {
  const work = await workMeta(params.id, { admin: true });
  if (!work) error(404, "no such work");
  const all = await workStills(params.id, { includeHidden: true, admin: true });
  // Stills a re-run dropped but kept for a review mark, lock, exclusion or
  // correction: not on the public side, not in "all", their own view.
  const notPicked = await workStills(params.id, { notPicked: true, admin: true });
  const show = url.searchParams.get("show") ?? "all";
  const stills =
    show === "unreviewed" ? all.filter((s) => s.review === "unreviewed" && !s.excluded)
    : show === "culled" ? all.filter((s) => s.review === "culled")
    : show === "hidden" ? all.filter((s) => s.excluded)
    : show === "repeats" ? all.filter((s) => s.repeatIn.length)
    : show === "notpicked" ? notPicked
    : all;
  return {
    work,
    show,
    stills,
    // The public sheet answers 404 for a work with nothing a visitor may see.
    publicSheet: all.some((s) => s.review !== "culled" && !s.excluded),
    // The episodes either side in the season, to walk without going back
    // to Review (the public sheet has the same walk).
    around: await workNeighbours(params.id, { admin: true }),
    // The other works of its series, which a still's repeat mark names.
    siblings: await workSiblings(params.id),
    // Whether "Re-read from Shoko" can work here (the title editor).
    shoko: shokoConfigured() && !work.local,
    // A locked not-picked still comes back with a re-run; not after a
    // discard (its extra frames are gone).
    discarded: show === "notpicked" ? await surplusDiscarded(params.id) : false,
    // The extraction's frames that never became stills (the pool view).
    poolLeft: (await poolNotInGallery(params.id)).frames.length,
    counts: {
      all: all.length,
      unreviewed: all.filter((s) => s.review === "unreviewed" && !s.excluded).length,
      culled: all.filter((s) => s.review === "culled").length,
      hidden: all.filter((s) => s.excluded).length,
      repeats: all.filter((s) => s.repeatIn.length).length,
      notPicked: notPicked.length,
    },
  };
}

const REVIEW_STATES = new Set(["kept", "culled", "unreviewed"]);
// What the selection bar can do to every selected still.
const BULK = {
  culled: (ids) => setReviewMany(ids, "culled"),
  kept: (ids) => setReviewMany(ids, "kept"),
  unreviewed: (ids) => setReviewMany(ids, "unreviewed"),
  lock: async (ids) => setLocks(ids, true),
  unlock: async (ids) => setLocks(ids, false),
  hide: (ids) => setFlagMany(ids, "excluded", true),
  show: (ids) => setFlagMany(ids, "excluded", false),
};
const SAID = { culled: "culled", kept: "kept", unreviewed: "set back to unreviewed", lock: "locked", unlock: "unlocked", hide: "hidden", show: "shown again" };

export const actions = {
  review: async ({ request, params }) => {
    const form = await request.formData();
    const id = String(form.get("id") ?? "");
    const state = String(form.get("state") ?? "");
    if (!id.startsWith(`${params.id}/`) || !REVIEW_STATES.has(state)) return fail(400, { message: "bad review request" });
    await setReview(id, state); // the note, if any, stays
    return { ok: true };
  },
  flag: async ({ request, params }) => {
    const form = await request.formData();
    const id = String(form.get("id") ?? "");
    const flag = String(form.get("flag") ?? "");
    const value = form.get("value") === "1";
    if (!id.startsWith(`${params.id}/`) || !["locked", "excluded"].includes(flag)) return fail(400, { message: "bad flag request" });
    if (!(await setFlag(id, flag, value))) return fail(409, { message: "That frame has no web image yet and this work's extra frames were discarded, so a lock could never be brought in by a re-run." });
    return { ok: true };
  },
  bulk: async ({ request, params }) => {
    const form = await request.formData();
    const op = String(form.get("op") ?? "");
    const ids = [...new Set(form.getAll("id").map(String))].filter((id) => id.startsWith(`${params.id}/`));
    if (!BULK[op] || !ids.length) return fail(400, { message: "nothing selected, or an unknown action" });
    const r = await BULK[op](ids);
    const n = typeof r === "number" ? r : r.changed.length;
    const no = typeof r === "number" ? 0 : r.refused.length;
    const refused = no ? ` ${no} not locked: no web image yet, and this work's extra frames were discarded.` : "";
    return { ok: true, message: `${n} still${n === 1 ? "" : "s"} ${SAID[op]}.${refused}` };
  },
  keepRest: async ({ params }) => {
    const { count } = await keepRest([params.id]);
    return { ok: true, message: count ? `Marked ${count} unreviewed still${count === 1 ? "" : "s"} as kept.` : "Nothing left to review here." };
  },
  // The episode title, typed. Kept beside Shoko's value, so a re-run's
  // import and a re-read leave it in place; an empty field clears it.
  title: async ({ request, params }) => {
    const form = await request.formData();
    const title = form.get("clear") === "1" ? "" : String(form.get("title") ?? "").trim().replace(/\s+/g, " ");
    if (title.length > 200) return fail(400, { message: "That title is longer than 200 characters." });
    await setEpisodeTitle(params.id, title || null);
    return { ok: true, message: title ? `Episode title set to “${title}”.` : "Correction cleared; the title is Shoko's again." };
  },
  // Shoko's title for this episode, read again (a GET, as onboarding
  // does). Shoko sometimes fills titles in after a show airs.
  reread: async ({ params }) => {
    const work = await workMeta(params.id, { admin: true });
    const id = work?.shokoEpisodeIds?.[0];
    if (id == null || work.local) return fail(400, { message: "This work has no Shoko episode id to read." });
    let name;
    try {
      const ep = await (await shoko()).episode(id);
      name = String(ep?.Name ?? "").trim() || null;
    } catch (e) {
      if (e instanceof ShokoError) return fail(502, { message: `Shoko could not be read: ${e.message}` });
      throw e;
    }
    await setEpisodeTitleShoko(params.id, name);
    const shown = work.episodeTitleHuman ? " Your correction still shows on top of it." : "";
    if (!name) return { ok: true, message: `Shoko has no title for this episode yet.${shown}` };
    return { ok: true, message: name === work.episodeTitleShoko ? `Shoko still says “${name}”.${shown}` : `Shoko now says “${name}”.${shown}` };
  },
};
