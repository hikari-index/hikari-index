import { explore, sample, searchByText } from "$lib/server/catalog.js";
import { encodeText, encoderUrl } from "$lib/server/encoder.js";

const PAGE_SIZE = 60;
const MOOD_POOL = 240; // ranked candidates fetched before the chips narrow them

// Search first, narrow with chips. Every state is in the URL (works
// without JavaScript, shareable, back button restores it).
//
// Two ways to read the search box (research/03: the UI reveals how it
// interpreted the query): "tags" matches words against tags and facet
// values; "mood" (mode=mood) ranks every still by how the picture reads,
// through the SigLIP text encoder, and the chips still narrow the ranked
// set. Mood needs the encoder service; without it the page says so.
export async function load({ url }) {
  const mood = url.searchParams.get("mode") === "mood";
  const q = (url.searchParams.get("q") || "").trim();
  const page = Math.max(1, Number(url.searchParams.get("page")) || 1);

  if (mood && q) {
    // Filters come from Explore with the query removed; ranking from the vector.
    const params = new URLSearchParams(url.searchParams);
    params.delete("q");
    const base = await explore(params);
    const encoded = await encodeText(q);
    // Rank inside the chips' pool: a chip's count is then what choosing it
    // yields (up to the pool size), not the global top 240 filtered after.
    const ranked = encoded ? await searchByText(encoded.embedding, { n: MOOD_POOL, ids: base.results.map((s) => s.id) }) : [];
    const pages = Math.max(1, Math.ceil(ranked.length / PAGE_SIZE));
    const at = Math.min(page, pages);
    return {
      facets: base.facets,
      counts: base.counts,
      tags: base.tags,
      active: { ...base.active, q },
      withoutQuery: null,
      interpreted: null,
      mode: "mood",
      moodPool: MOOD_POOL,
      moodUnavailable: !encoded,
      encoderConfigured: Boolean(encoderUrl()),
      frontDoor: false,
      browse: false,
      sample: [],
      total: ranked.length,
      page: at,
      pages,
      stills: ranked.slice((at - 1) * PAGE_SIZE, at * PAGE_SIZE),
    };
  }

  const { facets, counts, results, tags, withoutQuery, interpreted, active } = await explore(url.searchParams);
  const frontDoor = !active.q && !active.tags.length && !Object.keys(facets).some((f) => active[f]) && !url.searchParams.has("browse");
  const pages = Math.max(1, Math.ceil(results.length / PAGE_SIZE));
  const at = Math.min(page, pages);
  return {
    facets,
    counts,
    tags,
    active,
    withoutQuery,
    interpreted,
    // A mood with an empty box keeps the toggle on mood (front door or
    // filters only); ranking starts once there is a phrase.
    mode: mood ? "mood" : "tags",
    moodUnavailable: false,
    encoderConfigured: Boolean(encoderUrl()),
    frontDoor,
    browse: url.searchParams.has("browse"),
    sample: frontDoor ? await sample(24) : [],
    total: results.length,
    page: at,
    pages,
    stills: results.slice((at - 1) * PAGE_SIZE, at * PAGE_SIZE),
  };
}
