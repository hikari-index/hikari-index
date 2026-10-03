import { error, fail } from "@sveltejs/kit";
import { workMeta } from "$lib/server/catalog.js";
import { surplusDiscarded } from "$lib/server/discard.js";
import { pinPoolFrame, poolNotInGallery } from "$lib/server/pool.js";

// The pool: the extraction's frames that never became stills, each shown
// through a preview made from its master on request. Lock one and re-run
// the work (Jobs) to bring it in (ADR-0008 operator pin).
export async function load({ params }) {
  const work = await workMeta(params.id);
  if (!work) error(404, "no such work");
  const { frames, pool } = await poolNotInGallery(params.id);
  return {
    work,
    frames: frames.map(({ master, ...f }) => f), // the path stays on the server
    pool,
    discarded: await surplusDiscarded(params.id),
  };
}

export const actions = {
  pin: async ({ request, params }) => {
    const form = await request.formData();
    const candidate = String(form.get("candidate") ?? "");
    const r = await pinPoolFrame(params.id, candidate);
    if (r === "gone") return fail(400, { message: "That frame is not in this work's pool any more." });
    if (r === "discarded") return fail(409, { message: "This work's extra frames were discarded after review, and a re-run is refused after that, so a locked frame could not be brought in. Remove the work and onboard it again to choose from the whole pool." });
    return { ok: true, message: `${candidate} locked. Re-run the work (Jobs) to bring it in; until then it is listed under "not picked", and "done reviewing" waits for that re-run.` };
  },
};
