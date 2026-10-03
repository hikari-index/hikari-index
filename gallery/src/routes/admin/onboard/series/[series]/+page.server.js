import { error } from "@sveltejs/kit";
import { inArray } from "drizzle-orm";
import { db, schema } from "$lib/server/db/index.js";
import { chainsForFiles, estimates } from "$lib/server/jobs.js";
import { BATCH_MAX, fileProblems, mediaOf } from "$lib/server/onboard.js";
import { shoko, ShokoError } from "$lib/server/shoko.js";
import { shownEpisodeTitle } from "$lib/titles.js";

// Step two: one series' episodes and the files Shoko has for each. The
// operator ticks files (one, several or "all ready"); the confirm page
// re-reads them before anything is queued. "Ready" is research/01's
// auto-select rule: the episode has exactly one file, not a variation, with
// nothing blocking it. An episode with several files is never picked for
// the operator. Credits, trailers and parodies are counted, not listed.
const LISTED = new Set(["Episode", "Special", "Normal"]);

export async function load({ params }) {
  const id = Number(params.series);
  if (!Number.isInteger(id) || id <= 0) error(404, "no such series");
  let series, episodes;
  try {
    const c = await shoko();
    [series, episodes] = await Promise.all([c.series(id), c.episodes(id)]);
  } catch (e) {
    if (e instanceof ShokoError) return { error: e.message, series: null, episodes: [], hidden: 0 };
    throw e;
  }
  const files = episodes.flatMap((e) => e.Files || []);
  const fileIds = files.map((f) => f.ID).filter(Number.isInteger);
  const chains = await chainsForFiles(fileIds);
  const inGallery = new Map(
    fileIds.length
      ? (await db().select({ file: schema.works.shokoFileId, id: schema.works.id }).from(schema.works).where(inArray(schema.works.shokoFileId, fileIds))).map((r) => [r.file, r.id])
      : [],
  );
  const listed = episodes.filter((e) => LISTED.has(e.AniDB?.Type ?? "Episode"));
  const rows = listed
    .map((e) => ({
      id: e.IDs?.ID,
      type: e.AniDB?.Type ?? null,
      number: e.AniDB?.EpisodeNumber ?? null,
      title: shownEpisodeTitle(series?.AniDB?.Type === "Movie" ? "movie" : "episode", e.Name),
      aired: e.AniDB?.AirDate ?? null,
      files: (e.Files || []).map((f) => {
        const { blockers, notes } = fileProblems(f);
        const where = (f.Locations || []).find((l) => l.IsAccessible ?? l.Accessible) || (f.Locations || [])[0];
        return {
          id: f.ID,
          size: f.Size,
          // The file's name, so two releases of one episode can be told apart here
          name: String(where?.RelativePath || "").split(/[\\/]/).pop() || null,
          media: mediaOf(f),
          variation: Boolean(f.IsVariation),
          blockers,
          notes,
          work: inGallery.get(f.ID) ?? null,
          chain: chains.get(f.ID) ?? null,
        };
      }),
    }))
    .sort((a, b) => (a.type === b.type ? (a.number ?? 0) - (b.number ?? 0) : a.type === "Special" ? 1 : -1));
  for (const e of rows) {
    for (const f of e.files) {
      f.open = !f.work && !f.chain && !f.blockers.length;
      f.ready = f.open && e.files.length === 1 && !f.variation;
    }
  }
  return {
    error: null,
    estimate: await estimates(),
    batchMax: BATCH_MAX,
    series: { id, name: series.Name, type: series.AniDB?.Type ?? null, aired: series.AniDB?.AirDate ?? null },
    episodes: rows,
    hidden: episodes.length - listed.length,
  };
}
