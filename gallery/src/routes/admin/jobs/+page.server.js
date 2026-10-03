import { fail, redirect } from "@sveltejs/kit";
import { cancelChain, cancelWaiting, jobsOverview, listChains, repaletteAll, rerunFromAnalyze, retryStage } from "$lib/server/jobs.js";
import { BUDGETS } from "$lib/server/onboard.js";

// Every onboarding and where each of its stages stands. The page cancels
// and retries; the workers move the states.
export async function load({ url }) {
  // ?queued=N: the Onboard confirm page sends its count along, so arriving
  // here says what just happened.
  const queued = Math.min(Math.max(0, Math.trunc(Number(url.searchParams.get("queued")) || 0)), 999);
  return { ...(await jobsOverview()), queued, budgets: Object.fromEntries(Object.entries(BUDGETS).map(([k, v]) => [k, v.label])) };
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

async function idFrom(request, name) {
  const id = String((await request.formData()).get(name) ?? "");
  return UUID.test(id) ? id : null;
}

export const actions = {
  cancel: async ({ request }) => {
    const chain = await idFrom(request, "chain");
    if (!chain) return fail(400, { message: "bad chain id" });
    await cancelChain(chain);
    redirect(303, "/admin/jobs");
  },
  retry: async ({ request }) => {
    const stage = await idFrom(request, "stage");
    if (!stage) return fail(400, { message: "bad stage id" });
    const r = await retryStage(stage);
    if (r === "gone") return fail(409, { message: "That stage has already moved on; the page is up to date now." });
    if (r === "discarded") return fail(409, { message: "That stage cannot run again: the work's extra frames were discarded after review (it would need them)." });
    if (r === "taken") return fail(409, { message: "That extraction gave up, and its work id has since gone to another onboarding or work, so it cannot run again under it. Onboard the file again instead." });
    redirect(303, "/admin/jobs");
  },
  rerun: async ({ request, locals }) => {
    const form = await request.formData();
    const workId = String(form.get("work") ?? "");
    const choice = String(form.get("budget") ?? "");
    if (!Object.hasOwn(BUDGETS, choice)) return fail(400, { message: "Choose fewer, balanced or more." });
    const budget = { choice, factor: BUDGETS[choice].factor };
    const chain = await rerunFromAnalyze(workId, budget, locals.admin?.user ?? null);
    if (!chain) return fail(409, { message: `${workId} has a stage still open, no finished extraction to re-run from, or its extra frames were discarded after review (a re-run needs them).` });
    redirect(303, `/admin/jobs#${chain}`);
  },
  rerunAll: async ({ locals }) => {
    const finished = (await listChains()).filter((c) => c.rerunnable);
    let n = 0;
    for (const c of finished) if (await rerunFromAnalyze(c.workId, null, locals.admin?.user ?? null)) n += 1;
    return { ok: true, message: n ? `Queued ${n} re-run${n === 1 ? "" : "s"}, each at its own still count.` : "Nothing finished to re-run." };
  },
  // Palettes again for every work (palette-descriptor 0.2.0): see
  // jobs.repaletteAll for who gets what.
  repaletteAll: async ({ locals }) => {
    const n = await repaletteAll(locals.admin?.user ?? null);
    return { ok: true, message: n ? `${n} work${n === 1 ? "" : "s"} queued for the source worker, each followed by its import.` : "Nothing to do: every work is busy." };
  },
  cancelWaiting: async ({ request }) => {
    const form = await request.formData();
    const n = await cancelWaiting(form.getAll("chain").map(String));
    return { ok: true, message: n ? `Cancelled ${n} waiting onboarding${n === 1 ? "" : "s"}.` : "Nothing was waiting." };
  },
};
