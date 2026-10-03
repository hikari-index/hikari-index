import { error } from "@sveltejs/kit";
import { franchise } from "$lib/server/catalog.js";

export async function load({ params }) {
  const id = Number(params.id);
  const f = Number.isInteger(id) ? await franchise(id) : null;
  if (!f) error(404, "no such franchise");
  return { franchise: f };
}
