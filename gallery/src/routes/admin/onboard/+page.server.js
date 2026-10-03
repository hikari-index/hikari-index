import { inArray } from "drizzle-orm";
import { db, schema } from "$lib/server/db/index.js";
import { shoko, shokoConfigured, ShokoError } from "$lib/server/shoko.js";

// Step one of onboarding: find the series in Shoko (read-only search).
export async function load({ url }) {
  const q = (url.searchParams.get("q") || "").trim().slice(0, 200);
  const base = { configured: shokoConfigured(), q, results: [], error: null };
  if (!base.configured || !q) return base;
  try {
    const hits = await (await shoko()).searchSeries(q, 25);
    const ids = hits.map((h) => h.IDs?.ID).filter(Number.isInteger);
    const inGallery = new Set(
      ids.length
        ? (await db().select({ id: schema.works.shokoSeriesId }).from(schema.works).where(inArray(schema.works.shokoSeriesId, ids))).map((r) => r.id)
        : [],
    );
    return {
      ...base,
      results: hits.map((h) => ({
        id: h.IDs?.ID,
        name: h.Name,
        episodes: h.Sizes?.Local?.Episodes ?? null,
        specials: h.Sizes?.Local?.Specials ?? null,
        inGallery: inGallery.has(h.IDs?.ID),
      })),
    };
  } catch (error) {
    return { ...base, error: error instanceof ShokoError ? error.message : "Shoko did not answer." };
  }
}
