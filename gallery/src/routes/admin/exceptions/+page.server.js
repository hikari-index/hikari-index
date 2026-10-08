import { fail } from "@sveltejs/kit";
import { acceptSuggestion, DEFAULT_REASON_KINDS, exceptions, REASON_KINDS, setFlag, setReview, stillById } from "$lib/server/catalog.js";
import { reimportAll } from "$lib/server/jobs.js";

// Review by exception (research/03 "Metadata review"): the picked stills
// with a reason to look, instead of walking every sheet. ?k=text,rating
// picks those reasons (without it, the default ones; an unsure shot scale
// only when asked for); ?all=1 includes reviewed stills.
export async function load({ url }) {
  // An absent k means the defaults; an empty one (every chip off) means none.
  const k = url.searchParams.get("k");
  const kinds = k === null ? DEFAULT_REASON_KINDS : k.split(",").filter((x) => REASON_KINDS.includes(x));
  const all = url.searchParams.get("all") === "1";
  return { kinds, defaultKinds: DEFAULT_REASON_KINDS, all, ...(await exceptions({ kinds, all })) };
}

const REVIEW_STATES = new Set(["kept", "culled", "unreviewed"]);

export const actions = {
  review: async ({ request }) => {
    const form = await request.formData();
    const id = String(form.get("id") ?? "");
    const state = String(form.get("state") ?? "");
    if (!id.includes("/") || !REVIEW_STATES.has(state)) return fail(400, { message: "bad review request" });
    await setReview(id, state);
    return { ok: true };
  },
  flag: async ({ request }) => {
    const form = await request.formData();
    const id = String(form.get("id") ?? "");
    const flag = String(form.get("flag") ?? "");
    if (!id.includes("/") || !["locked", "excluded"].includes(flag)) return fail(400, { message: "bad flag request" });
    if (!(await setFlag(id, flag, form.get("value") === "1"))) return fail(409, { message: "That frame has no web image yet and this work's extra frames were discarded, so a lock could never be brought in by a re-run." });
    return { ok: true };
  },
  // Accept a suggested label (#41): the value the button showed, and only
  // while the still's own reason still suggests it (a re-run in between
  // may have changed it) and no size has been set on the still since.
  accept: async ({ request }) => {
    const form = await request.formData();
    const id = String(form.get("id") ?? "");
    const family = String(form.get("family") ?? "");
    const value = String(form.get("value") ?? "");
    const still = id.includes("/") ? await stillById(id, { admin: true }) : null;
    const suggested = still?.suggested?.[family];
    if (!suggested) return fail(400, { message: "nothing suggested for that still" });
    if (suggested.value !== value) return fail(409, { message: `Not accepted: the suggestion is now ${suggested.value.replaceAll("-", " ")}, not ${value.replaceAll("-", " ")} (the work was re-run). Look again.` });
    if (!(await acceptSuggestion(id, family, value))) return fail(409, { message: "Not accepted: a size was set on this still since this page loaded, or a re-run removed it. Reload." });
    return { ok: true };
  },
  // Read every work's run records again (the reasons were added after
  // most works were imported). Queues one import stage per work; the
  // Jobs page shows them going through.
  reread: async ({ locals }) => {
    const n = await reimportAll(locals.admin?.user ?? "admin");
    return { ok: true, message: n ? `Queued ${n} import${n === 1 ? "" : "s"}; the reasons fill in as each one finishes (Jobs).` : "No work has run records to read, or every one is busy." };
  },
};
