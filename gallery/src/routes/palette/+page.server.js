import { error } from "@sveltejs/kit";
import { hexToOklab } from "$lib/color.js";
import { allVisibleStills, visibleStillById, workIdsInScope, workMeta } from "$lib/server/catalog.js";
import { MATCH, cloud, gradePick, grades, nearest, orderChips, pick, toneName, toneSwatch } from "$lib/server/palette.js";

const HEX = /^#?[0-9a-fA-F]{6}$/;
const TONES = new Set(["warm", "cool", "neutral", "green", "magenta"]);
const PAGE_SIZE = 60;
const norm = (c) => (c && HEX.test(c) ? "#" + c.replace("#", "").toUpperCase() : null);

// /palette                                   Grades: common shadow/highlight pairs + the two swatch rows
// /palette?shadow=HEX&highlight=HEX          stills graded that way (either may be omitted)
// /palette?shadows=cool&highlights=warm      the same by tone name (from a tile)
// /palette?view=colors                      the color cloud (browse by one color)
// /palette?c=HEX[&c=HEX]&match=strict|broad  stills containing each color
// /palette?near=<still id>[&scope=…]         similar by palette to a still
export async function load({ url }) {
  const stills = await allVisibleStills();
  const labels = new Map();
  const withLabel = async (s) => {
    if (!labels.has(s.workId)) labels.set(s.workId, (await workMeta(s.workId))?.label ?? s.workId);
    return { ...s, workLabel: labels.get(s.workId) };
  };
  const page = Math.max(1, Number(url.searchParams.get("page")) || 1);
  const paged = async (results) => {
    const pages = Math.max(1, Math.ceil(results.length / PAGE_SIZE));
    const at = Math.min(page, pages); // a stale page number shows the last page, not an empty one
    const slice = results.slice((at - 1) * PAGE_SIZE, at * PAGE_SIZE);
    return { results: await Promise.all(slice.map(withLabel)), total: results.length, page: at, pages };
  };

  const near = url.searchParams.get("near");
  if (near) {
    const target = await visibleStillById(near);
    if (!target) error(404, "no such still");
    const scope = ["franchise", "season", "work"].includes(url.searchParams.get("scope")) ? url.searchParams.get("scope") : "all";
    const inScope = await workIdsInScope(target.workId, scope);
    const pool = inScope ? stills.filter((s) => inScope.includes(s.workId)) : stills;
    const results = await Promise.all(nearest(target, pool).map((r) => withLabel({ ...r.still, distance: r.distance })));
    return { mode: "near", target: await withLabel(target), scope, results };
  }

  const chosen = url.searchParams.getAll("c").map(norm).filter(Boolean);
  if (chosen.length) {
    const match = url.searchParams.get("match") === "broad" ? "broad" : "strict";
    const results = pick(stills, chosen, match);
    // Colours to add: the cloud's, counted within the current results so
    // a chip says what narrowing to it would leave; the chosen ones aside.
    const more = orderChips(cloud(results, { minStills: 2 }), "count")
      .filter((c) => !chosen.includes(c.hex))
      .slice(0, 24)
      .map((c) => ({ ...c, count: pick(results, [c.hex], match).length })) // counted the way the click matches
      .filter((c) => c.count);
    return { mode: "pick", chosen, match, limits: MATCH, more, ...(await paged(results)), all: stills.length };
  }

  const shadow = norm(url.searchParams.get("shadow"));
  const highlight = norm(url.searchParams.get("highlight"));
  const shadowTone = TONES.has(url.searchParams.get("shadows")) ? url.searchParams.get("shadows") : null;
  const highlightTone = TONES.has(url.searchParams.get("highlights")) ? url.searchParams.get("highlights") : null;
  if (shadow || highlight || shadowTone || highlightTone) {
    const results = gradePick(stills, { shadow, highlight, shadowTone, highlightTone });
    // Each row's counts are over the stills the OTHER lane's choice leaves,
    // so a count says what adding that swatch would give. The swatches
    // themselves stay the library's, so the chosen one keeps its mark.
    const { shadows, highlights } = grades(stills, {
      shadowPool: highlight || highlightTone ? gradePick(stills, { highlight, highlightTone }) : stills,
      highlightPool: shadow || shadowTone ? gradePick(stills, { shadow, shadowTone }) : stills,
      shadow,
      highlight,
    });
    return {
      mode: "grade",
      shadow,
      highlight,
      shadowTone,
      highlightTone,
      shadowName: shadow ? toneName(hexToOklab(shadow)) : shadowTone,
      highlightName: highlight ? toneName(hexToOklab(highlight)) : highlightTone,
      // What the heading's pair shows for a lane chosen by tone.
      shadowToneHex: !shadow && shadowTone ? toneSwatch(results, "shadows") : null,
      highlightToneHex: !highlight && highlightTone ? toneSwatch(results, "highlights") : null,
      shadows,
      highlights,
      ...(await paged(results)),
      all: stills.length,
    };
  }

  if (url.searchParams.get("view") === "colors") {
    const order = url.searchParams.get("order") === "count" ? "count" : "color";
    return { mode: "cloud", order, chips: orderChips(cloud(stills), order), all: stills.length };
  }

  return { mode: "grades", ...grades(stills), all: stills.length };
}
