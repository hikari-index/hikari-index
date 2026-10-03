import { error } from "@sveltejs/kit";
import { workMeta, workNeighbours, workStills } from "$lib/server/catalog.js";

// The episode contact sheet (research/03): the selected stills in
// chronological order with timeline position and key labels. Review and
// editing live under /admin; this page carries no form actions.
export async function load({ params }) {
  const work = await workMeta(params.work);
  if (!work) error(404, "no such work");
  const stills = await workStills(params.work);
  // A work whose stills are all culled or hidden is not in the Library;
  // its address says no more than the Library does.
  if (!stills.length) error(404, "no such work");
  const timed = stills.filter((s) => s.ts != null);
  return {
    work,
    around: await workNeighbours(params.work),
    stills,
    span: timed.length ? timed[timed.length - 1].ts - timed[0].ts : null,
  };
}
