import { error } from "@sveltejs/kit";
import { hasEmbedding, similarTo, visibleStillById, workMeta } from "$lib/server/catalog.js";

const SCOPES = ["all", "franchise", "season", "work"];

// ?scope=all (default) | franchise | season | work (this episode)
export async function load({ params, url }) {
  const id = `${params.work}/${params.candidate}`;
  const target = await visibleStillById(id);
  if (!target) error(404, "no such still");
  const scope = SCOPES.includes(url.searchParams.get("scope")) ? url.searchParams.get("scope") : "all";
  return {
    target,
    work: await workMeta(params.work),
    scope,
    results: await similarTo(id, { scope }),
    // An empty result can mean no embedding or nothing else in the scope;
    // the page says which.
    embedded: await hasEmbedding(id),
  };
}
