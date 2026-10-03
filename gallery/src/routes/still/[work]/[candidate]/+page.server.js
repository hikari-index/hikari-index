import { error } from "@sveltejs/kit";
import { visibleStillById, workMeta, workStills } from "$lib/server/catalog.js";

// A still links to its chronological neighbours among the visible stills
// of the same work (research/03: adjacent selected frames, not a player).
export async function load({ params }) {
  const id = `${params.work}/${params.candidate}`;
  const still = await visibleStillById(id);
  if (!still) error(404, "no such still");
  const work = await workMeta(params.work);
  const visible = await workStills(params.work);
  const index = visible.findIndex((s) => s.id === id);
  return {
    work,
    still,
    prev: index > 0 ? visible[index - 1] : null,
    next: index >= 0 && index < visible.length - 1 ? visible[index + 1] : null,
    position: index >= 0 ? index + 1 : null,
    total: visible.length,
  };
}
