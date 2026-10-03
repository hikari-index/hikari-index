import { error } from "@sveltejs/kit";
import { hexToOklab } from "$lib/color.js";
import { stillById, workMeta } from "$lib/server/catalog.js";
import { toneName } from "$lib/server/palette.js";

// The post card maker: one still, matted, with its palette, shadow /
// midtone / highlight swatches and the show's name, drawn in the browser and
// saved as a PNG. Nothing is posted; the owner downloads the file.
const DATA = ["shot_scale", "setting", "time", "weather", "lighting"];
const pretty = (v) => String(v).replaceAll("-", " ").replaceAll("_", " ");

export async function load({ params }) {
  const still = await stillById(`${params.work}/${params.candidate}`, { admin: true });
  if (!still) error(404, "no such still");
  if (!still.tiers?.length) error(409, "This frame has no web image yet (a locked pool frame waits for a re-run of its work), so there is nothing to make a card from.");
  const work = await workMeta(params.work);
  const p = still.palette;
  const sh = p?.shadows?.[0]?.hex;
  const hi = p?.highlights?.[0]?.hex;
  const grade = sh && hi ? `${toneName(hexToOklab(sh))} shadows, ${toneName(hexToOklab(hi))} highlights` : null;
  const facts = DATA.map((f) => still.facets[f]).filter((v) => v && !["none-visible", "unknown", "mixed"].includes(v)).map(pretty);
  return { still, work, grade, facts };
}
