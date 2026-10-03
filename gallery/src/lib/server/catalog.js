// Read model for the pages, over the database. Shapes are the ones the
// pages were first written against (still.workId, still.tiers, ...).
//
// The library is small (tens of stills per work, a handful of works), so
// facet filtering and counting run over the fetched rows. When that stops
// being true the queries move into SQL; what the pages receive stays the same.
import { and, asc, cosineDistance, eq, getTableColumns, isNotNull, sql } from "drizzle-orm";
import { db, schema } from "$lib/server/db/index.js";
import { shownEpisodeTitle } from "$lib/titles.js";
import { cullableRepeats } from "./repeats.js";

const { works: worksTable, stills: stillsTable, franchises: franchisesTable, seasons: seasonsTable } = schema;

// Every column but the embedding (768 numbers a row) for the pages that
// never read it: the browse pages read thousands of rows at once.
const stillColumns = (({ embedding, ...rest }) => rest)(getTableColumns(stillsTable));
// The same columns for a raw select, as "s.col, s.col, ..." (snake_case).
const stillSelect = (alias) => Object.values(stillColumns).map((c) => `${alias}.${c.name}`).join(", ");

// Facets and tags as shown: the machine's proposal with the owner's
// corrections applied (a null override clears a machine value).
export function mergeFacets(machine, human) {
  const out = { ...(machine ?? {}) };
  for (const [k, v] of Object.entries(human ?? {})) {
    if (v == null || v === "") delete out[k];
    else out[k] = v;
  }
  return out;
}
export function mergeTags(machine, human) {
  const remove = new Set(human?.remove ?? []);
  const out = (machine ?? []).filter((t) => !remove.has(t));
  for (const t of human?.add ?? []) if (!out.includes(t)) out.push(t);
  return out.sort();
}

// A raw `select *` row (snake_case) as the camelCase row drizzle's
// select gives, for the few SQL-built queries here.
function fromRow(r) {
  return {
    id: r.id, workId: r.work_id, candidateId: r.candidate_id, shotId: r.shot_id, tsSeconds: r.ts_seconds,
    source: r.source, selected: r.selected, facets: r.facets, tags: r.tags, tiers: r.tiers, palette: r.palette,
    facetsHuman: r.facets_human, tagsHuman: r.tags_human, reviewState: r.review_state, locked: r.locked,
    excluded: r.excluded, reviewNote: r.review_note, reviewReasons: r.review_reasons, repeatIn: r.repeat_in,
  };
}

function toStill(row, admin = false) {
  const still = {
    id: row.id,
    workId: row.workId,
    candidate: row.candidateId,
    shot: row.shotId,
    ts: row.tsSeconds,
    source: row.source,
    selected: row.selected,
    facets: mergeFacets(row.facets, row.facetsHuman),
    tags: mergeTags(row.tags, row.tagsHuman),
    tiers: row.tiers ?? [],
    palette: row.palette ?? null,
  };
  if (!admin) return still; // the public payload carries no review or correction internals
  return {
    ...still,
    machine: { facets: row.facets ?? {}, tags: row.tags ?? [] },
    human: { facets: row.facetsHuman ?? {}, tags: row.tagsHuman ?? { add: [], remove: [] } },
    review: row.reviewState,
    reviewNote: row.reviewNote ?? null,
    locked: row.locked,
    excluded: row.excluded,
    reasons: row.reviewReasons ?? [],
    // The sibling works this frame repeats in (repeats.js); a mark only.
    repeatIn: row.repeatIn ?? [],
    // The tagger's rating reason, as a mark on the card.
    sensitive: (row.reviewReasons ?? []).some((r) => r.k === "rating"),
  };
}

export const REASON_KINDS = ["text", "rating", "unsure"];

// The picked stills with a reason to look, grouped by work in library
// order. kinds: which reasons count; all: reviewed ones too (the default
// is the unreviewed, not hidden ones, the review-by-exception list).
export async function exceptions({ kinds = REASON_KINDS, all = false } = {}) {
  const wanted = new Set(REASON_KINDS.filter((k) => kinds.includes(k)));
  const scope = all ? sql`true` : sql`s.review_state = 'unreviewed' and not s.excluded`;
  const every = (await db().execute(sql`select ${sql.raw(stillSelect("s"))} from stills s
    where s.selected and jsonb_array_length(s.review_reasons) > 0 and ${scope}
    order by s.work_id, s.ts_seconds nulls last, s.candidate_id`)).rows;
  // The chips count every reason in scope, whatever the filter shows.
  const counts = {};
  for (const k of REASON_KINDS) counts[k] = 0;
  const kindsOf = (r) => new Set((r.review_reasons || []).map((x) => x.k));
  for (const r of every) for (const k of kindsOf(r)) counts[k] += 1;
  const rows = every.filter((r) => [...kindsOf(r)].some((k) => wanted.has(k)));
  const labels = new Map((await db().select().from(worksTable)).map((w) => [w.id, toWorkMeta(w)]));
  const byWork = new Map();
  for (const r of rows) {
    if (!byWork.has(r.work_id)) byWork.set(r.work_id, []);
    byWork.get(r.work_id).push(toStill(fromRow(r), true));
  }
  const groups = [...byWork].map(([id, stills]) => ({ work: labels.get(id), stills }))
    .filter((g) => g.work)
    .sort((a, b) => a.work.title.localeCompare(b.work.title) || byEpisode(a.work, b.work));
  return { groups, counts, total: rows.length };
}

// How many unreviewed stills carry a reason (the Review page's link).
export async function exceptionCount() {
  const r = await db().execute(sql`select count(*)::int as n from stills s
    where s.selected and s.review_state = 'unreviewed' and not s.excluded and jsonb_array_length(s.review_reasons) > 0`);
  return r.rows[0]?.n ?? 0;
}

// admin: adds what only the workbench's title editor needs (both sides of
// the episode title, Shoko's episode ids, whether the work was added from a
// local file). Public payloads never carry those. An options object, not a
// bare flag: `rows.map((row) => toWorkMeta(row))` passes the index as the second argument.
function toWorkMeta(row, { admin = false } = {}) {
  const episode =
    row.entryType === "episode" && row.episode != null
      ? `E${String(row.episode).padStart(2, "0")}`
      : null;
  const episodeTitle = shownEpisodeTitle(row.entryType, row.episodeTitleHuman ?? row.episodeTitle);
  return {
    id: row.id,
    title: row.title,
    episode,
    episodeNumber: row.episode,
    episodeTitle,
    ...(admin
      ? {
          // Both sides of the title, for the workbench's editor.
          episodeTitleShoko: row.episodeTitle ?? null,
          episodeTitleHuman: row.episodeTitleHuman ?? null,
          shokoEpisodeIds: row.shokoEpisodeIds ?? [],
          // Added from a local file, without Shoko (ADR-0014): its ids are
          // the gallery's own, below zero, and Shoko has nothing to re-read.
          local: (row.shokoSeriesId ?? 0) < 0,
        }
      : {}),
    entryType: row.entryType,
    label: [row.title, episode, episodeTitle].filter(Boolean).join(" · "),
  };
}

// The owner's episode title; null clears it and Shoko's shows again.
export async function setEpisodeTitle(workId, title) {
  await db().update(worksTable).set({ episodeTitleHuman: title }).where(eq(worksTable.id, workId));
}

// Shoko's episode title, read again: the machine value. A correction, if
// any, stays on top of it.
export async function setEpisodeTitleShoko(workId, title) {
  await db().update(worksTable).set({ episodeTitle: title }).where(eq(worksTable.id, workId));
}

// Episodes in numeric order (E100 after E11), films and unnumbered
// entries after them, by label.
const byEpisode = (a, b) =>
  (a.episodeNumber ?? 1e9) - (b.episodeNumber ?? 1e9) || a.label.localeCompare(b.label);

// Chronological order; stills without a timestamp sort last, by id.
const chronological = [sql`${stillsTable.tsSeconds} asc nulls last`, asc(stillsTable.id)];

// "Visible" = what an ordinary browse shows: chosen by the selector, not
// culled by review, not excluded by the operator.
function visibleWhere(workId) {
  const clauses = [
    eq(stillsTable.selected, true),
    sql`${stillsTable.reviewState} <> 'culled'`,
    eq(stillsTable.excluded, false),
  ];
  if (workId) clauses.push(eq(stillsTable.workId, workId));
  return and(...clauses);
}

export async function workMeta(workId, { admin = false } = {}) {
  const [row] = await db().select().from(worksTable).where(eq(worksTable.id, workId));
  return row ? toWorkMeta(row, { admin }) : null;
}

// Whether a still is one an ordinary visitor may see (picked, not culled,
// not hidden): the rule the pages use, for the image route.
export async function stillIsVisible(workId, candidateId) {
  const r = await db().execute(sql`select 1 from stills where work_id = ${workId} and candidate_id = ${candidateId}
    and selected and review_state <> 'culled' and not excluded limit 1`);
  return r.rows.length > 0;
}

// includeHidden: the review view shows culled/excluded stills too, marked.
// notPicked: the rows a re-run dropped but kept for their review mark,
// lock, exclusion or correction (import sets them selected = false).
export async function workStills(workId, { includeHidden = false, admin = false, notPicked = false } = {}) {
  const where = notPicked
    ? and(eq(stillsTable.workId, workId), eq(stillsTable.selected, false))
    : includeHidden
      ? and(eq(stillsTable.workId, workId), eq(stillsTable.selected, true))
      : visibleWhere(workId);
  const rows = await db().select(stillColumns).from(stillsTable).where(where).orderBy(...chronological);
  return rows.map((r) => toStill(r, admin));
}

export async function stillById(id, { admin = false } = {}) {
  const [row] = await db().select().from(stillsTable).where(eq(stillsTable.id, id));
  return row ? toStill(row, admin) : null;
}

// The library, grouped the way Shoko groups it (franchise → season →
// episode) but showing only what the gallery holds. It is a curated subset
// of the media library, never a mirror of it: seasons and episodes with no
// stills do not appear, and no library inventory (what exists, what is
// missing) is exposed.
export async function library() {
  const worksRows = await db().select().from(worksTable);
  const seasonRows = await db().select().from(seasonsTable);
  const franchiseRows = await db().select().from(franchisesTable).orderBy(asc(franchisesTable.sortName), asc(franchisesTable.name));
  const countRows = await db()
    .select({ workId: stillsTable.workId, count: sql`count(*)::int` })
    .from(stillsTable)
    .where(visibleWhere())
    .groupBy(stillsTable.workId);
  const counts = new Map(countRows.map((r) => [r.workId, r.count]));
  // One cover per work: the earliest visible still, chosen in SQL rather
  // than by reading every visible row.
  const coverRows = (await db().execute(sql`select distinct on (work_id) * from stills
    where selected and review_state <> 'culled' and not excluded
    order by work_id, ts_seconds asc nulls last, id`)).rows;
  const covers = new Map(coverRows.map((r) => [r.work_id, toStill(fromRow(r))]));

  const worksBySeries = new Map();
  for (const w of worksRows) {
    if (!counts.get(w.id)) continue;
    const list = worksBySeries.get(w.shokoSeriesId) ?? [];
    list.push({ ...toWorkMeta(w), count: counts.get(w.id), cover: covers.get(w.id) });
    worksBySeries.set(w.shokoSeriesId, list);
  }
  for (const list of worksBySeries.values()) list.sort(byEpisode);

  const seasonsByFranchise = new Map();
  for (const s of seasonRows) {
    const works = worksBySeries.get(s.id);
    if (!works) continue; // nothing extracted from this season: it does not exist here
    const list = seasonsByFranchise.get(s.franchiseId) ?? [];
    list.push({ id: s.id, name: s.name, type: s.anidbType, airDate: s.airDate, works });
    seasonsByFranchise.set(s.franchiseId, list);
  }
  const placed = new Set(seasonRows.map((s) => s.id));

  const out = [];
  for (const f of franchiseRows) {
    const list = (seasonsByFranchise.get(f.id) ?? []).sort((a, b) => (a.airDate ?? "9999").localeCompare(b.airDate ?? "9999") || a.name.localeCompare(b.name));
    if (!list.length) continue;
    const stills = list.reduce((n, s) => n + s.works.reduce((m, w) => m + w.count, 0), 0);
    const cover = list.flatMap((s) => s.works).find((w) => w.cover)?.cover ?? null;
    out.push({ id: f.id, name: f.name, seasons: list, stills, cover });
  }
  const unsorted = [...worksBySeries.entries()].filter(([sid]) => !placed.has(sid)).flatMap(([, ws]) => ws);
  return { franchises: out, unsorted };
}

// Where a work sits: its title (Shoko group) and season, and the episodes
// before and after it that have stills, so a sheet can walk the season.
// admin: siblings are works with any picked still (the workbench walks
// to an episode whose stills are all hidden or culled; the public sheet
// does not).
export async function workNeighbours(workId, { admin = false } = {}) {
  const [w] = await db().select().from(worksTable).where(eq(worksTable.id, workId));
  if (!w) return null;
  const season = w.shokoSeriesId != null ? (await db().select().from(seasonsTable).where(eq(seasonsTable.id, w.shokoSeriesId)))[0] : null;
  const fr = season ? (await db().select().from(franchisesTable).where(eq(franchisesTable.id, season.franchiseId)))[0] : null;
  let prev = null;
  let next = null;
  if (w.shokoSeriesId != null) {
    const counts = new Map((await db()
      .select({ workId: stillsTable.workId, count: sql`count(*)::int` })
      .from(stillsTable)
      .where(admin ? eq(stillsTable.selected, true) : visibleWhere())
      .groupBy(stillsTable.workId)).map((r) => [r.workId, r.count]));
    const siblings = (await db().select().from(worksTable).where(eq(worksTable.shokoSeriesId, w.shokoSeriesId)))
      .map((row) => toWorkMeta(row))
      .filter((m) => m.id === workId || counts.get(m.id))
      .sort(byEpisode);
    const i = siblings.findIndex((m) => m.id === workId);
    prev = i > 0 ? siblings[i - 1] : null;
    next = i >= 0 && i < siblings.length - 1 ? siblings[i + 1] : null;
  }
  return {
    franchise: fr ? { id: fr.id, name: fr.name } : null,
    season: season ? { id: season.id, name: season.name } : null,
    prev,
    next,
  };
}

export async function franchise(id) {
  const { franchises: all } = await library();
  return all.find((f) => f.id === id) ?? null;
}

export async function works() {
  const rows = await db().select().from(worksTable).orderBy(asc(worksTable.title), asc(worksTable.episode));
  const out = [];
  for (const row of rows) {
    const stills = await workStills(row.id);
    if (!stills.length) continue;
    out.push({ ...toWorkMeta(row), cover: stills[0], count: stills.length });
  }
  return out;
}

export async function allVisibleStills() {
  return visibleStills();
}

async function visibleStills() {
  const rows = await db().select(stillColumns).from(stillsTable).where(visibleWhere()).orderBy(...chronological);
  // Not `rows.map(toStill)`: map's index would arrive as `admin` and every
  // still after the first would carry its review internals to the public.
  return rows.map((row) => toStill(row));
}

function facetFamilies(stills) {
  const values = {};
  for (const s of stills) {
    for (const [name, value] of Object.entries(s.facets)) {
      (values[name] ??= new Set()).add(value);
    }
  }
  return Object.fromEntries(
    Object.keys(values).sort().map((k) => [k, [...values[k]].sort()])
  );
}

function matches(still, wanted, query) {
  for (const [name, value] of Object.entries(wanted)) {
    if (still.facets[name] !== value) return false;
  }
  if (query && !still.tags.some((t) => t.includes(query))) return false;
  return true;
}

// Explore: search first (tags today; text/mood search when the encoder
// exists), then narrow with chips. Returns the results plus, for every
// facet family and for tags, the values that remain under the current
// constraints with their counts, so the page can offer only chips that
// lead somewhere (shot.cafe's related-tag row).
export async function explore(params) {
  const stills = await visibleStills();
  const worksRows = await db().select().from(worksTable);
  const labels = new Map(worksRows.map((w) => [w.id, toWorkMeta(w).label]));
  const arrived = new Map(worksRows.map((w) => [w.id, w.importedAt ? new Date(w.importedAt).getTime() : 0]));
  for (const s of stills) {
    s.workLabel = labels.get(s.workId) ?? s.workId;
    s.workArrived = arrived.get(s.workId) ?? 0;
  }
  const facets = facetFamilies(stills);
  const wanted = {};
  for (const name of Object.keys(facets)) {
    const value = params.get(name);
    if (value) wanted[name] = value;
  }
  let query = (params.get("q") || "").trim().toLowerCase();
  const tagsWanted = params.getAll("tag").map((t) => t.toLowerCase()).filter(Boolean);
  // A typed word that is exactly a facet value ("night", "low key") is
  // that facet, and the page says so (research/03: reveal how the query
  // was interpreted). Only when the family is not already constrained.
  let interpreted = null;
  const norm = (v) => v.toLowerCase().replaceAll("-", " ").replaceAll("_", " ");
  for (const [family, values] of Object.entries(facets)) {
    if (wanted[family]) continue;
    const hit = values.find((v) => norm(v) === norm(query));
    if (hit) {
      wanted[family] = hit;
      interpreted = { query, family, value: hit };
      query = "";
      break;
    }
  }
  const match = (s, w, q, tags) =>
    matches(s, w, q) && tags.every((t) => s.tags.includes(t));
  // Results read by work, newest import first (an alphabet is no order for
  // a gallery; what arrived last is what you have not seen), each work in
  // its own time order: a still's time means nothing across works, so the
  // old global time order interleaved twenty works by it.
  const results = stills
    .filter((s) => match(s, wanted, query, tagsWanted))
    .sort((a, b) => b.workArrived - a.workArrived || a.workLabel.localeCompare(b.workLabel) || a.workId.localeCompare(b.workId) || (a.ts ?? 1e9) - (b.ts ?? 1e9) || a.id.localeCompare(b.id));

  // Per-family counts under every OTHER active constraint (so a chip shows
  // what choosing it would yield, and the active value keeps its count).
  const counts = {};
  for (const family of Object.keys(facets)) {
    const bucket = (counts[family] = {});
    const others = Object.fromEntries(Object.entries(wanted).filter(([n]) => n !== family));
    for (const s of stills) {
      if (!match(s, others, query, tagsWanted)) continue;
      const value = s.facets[family];
      if (value) bucket[value] = (bucket[value] || 0) + 1;
    }
  }
  // Tag counts within the current results, minus tags already chosen.
  const tagCounts = {};
  for (const s of results) {
    for (const tag of s.tags) {
      if (tagsWanted.includes(tag)) continue;
      tagCounts[tag] = (tagCounts[tag] || 0) + 1;
    }
  }
  const tags = Object.entries(tagCounts)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 24)
    .map(([tag, count]) => ({ tag, count }));

  // Zero results: what dropping the search alone would give (research/03:
  // "a zero-result page suggests which constraint caused the empty intersection").
  const withoutQuery = query && !results.length ? stills.filter((s) => match(s, wanted, "", tagsWanted)).length : null;
  return { facets, counts, results, tags, withoutQuery, interpreted, active: { ...wanted, tags: tagsWanted, q: query } };
}

// Front door: a small sample spread across works, stable for a day so the
// page does not shuffle on every reload. Skips the first two minutes of an
// episode so studio logos and title cards do not lead.
export async function sample(n = 24) {
  const stills = await visibleStills();
  const rows = await db().select().from(worksTable);
  const labels = new Map(rows.map((w) => [w.id, toWorkMeta(w).label]));
  // One slot per title, not per episode: a season of twelve is one title.
  const titles = new Map(rows.map((w) => [w.id, w.title]));
  const day = Math.floor(Date.now() / 86400000);
  const byWork = new Map();
  for (const s of stills) {
    if (s.ts != null && s.ts < 120) continue;
    const key = titles.get(s.workId) ?? s.workId;
    if (!byWork.has(key)) byWork.set(key, []);
    byWork.get(key).push(s);
  }
  const out = [];
  const sorted = [...byWork.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  // Start from a different work each day; with more works than slots the
  // first two dozen in the alphabet were the only ones ever shown.
  const start = sorted.length ? (day * 7) % sorted.length : 0;
  const works = sorted.slice(start).concat(sorted.slice(0, start));
  for (let round = 0; out.length < n && round < 4; round++) {
    for (const [, list] of works) {
      if (out.length >= n) break;
      const pick = list[(day * 7 + round * 13 + list.length) % list.length];
      if (pick && !out.includes(pick)) out.push(pick);
    }
  }
  for (const s of out) s.workLabel = labels.get(s.workId) ?? s.workId;
  return out;
}

// "Find similar" from a still (research/02): exact cosine search over the
// stored SigLIP vectors of visible stills. No approximate index until a
// measurement says one is needed. Returns [] when the still has no vector.
// scope: "all" | "franchise" (same Shoko top-level group) | "season" (same
// Shoko series) | "work" (this episode/movie).
export async function similarTo(id, { n = 24, scope = "all" } = {}) {
  const [target] = await db()
    .select({
      embedding: stillsTable.embedding,
      workId: stillsTable.workId,
      seriesId: worksTable.shokoSeriesId,
      franchiseId: seasonsTable.franchiseId,
    })
    .from(stillsTable)
    .innerJoin(worksTable, eq(worksTable.id, stillsTable.workId))
    .leftJoin(seasonsTable, eq(seasonsTable.id, worksTable.shokoSeriesId))
    .where(eq(stillsTable.id, id));
  if (!target?.embedding) return [];
  const distance = cosineDistance(stillsTable.embedding, target.embedding);
  const clauses = [visibleWhere(), isNotNull(stillsTable.embedding), sql`${stillsTable.id} <> ${id}`];
  if (scope === "work") clauses.push(eq(stillsTable.workId, target.workId));
  if (scope === "season") clauses.push(eq(worksTable.shokoSeriesId, target.seriesId));
  if (scope === "franchise") {
    // Without a synced franchise, fall back to the season so the answer is
    // never "everything" when the user asked for less.
    clauses.push(target.franchiseId == null
      ? eq(worksTable.shokoSeriesId, target.seriesId)
      : eq(seasonsTable.franchiseId, target.franchiseId));
  }
  const rows = await db()
    .select({ row: stillsTable, work: worksTable, distance })
    .from(stillsTable)
    .innerJoin(worksTable, eq(worksTable.id, stillsTable.workId))
    .leftJoin(seasonsTable, eq(seasonsTable.id, worksTable.shokoSeriesId))
    .where(and(...clauses))
    .orderBy(distance)
    .limit(n);
  return rows.map(({ row, work, distance: d }) => ({
    ...toStill(row),
    work: toWorkMeta(work),
    similarity: 1 - Number(d),
  }));
}

// Mood search (research/02): rank visible stills by cosine similarity to a
// text vector from the SigLIP text tower, optionally within a set of works
// or under the same facet/tag constraints Explore applies. Exact search,
// same as similarTo; no approximate index until measured.
// `ids` limits the ranking to those stills (the Explore chips' pool), so
// a chip with a count yields that many, not the global top n filtered
// afterwards.
export async function searchByText(vector, { n = 60, workIds = null, ids = null } = {}) {
  const distance = cosineDistance(stillsTable.embedding, vector);
  const clauses = [visibleWhere(), isNotNull(stillsTable.embedding)];
  if (Array.isArray(workIds)) {
    if (!workIds.length) return [];
    clauses.push(sql`${stillsTable.workId} in ${workIds}`);
  }
  if (Array.isArray(ids)) {
    if (!ids.length) return [];
    clauses.push(sql`${stillsTable.id} = any(${sql.param(ids)}::text[])`);
  }
  const rows = await db()
    .select({ row: stillsTable, work: worksTable, distance })
    .from(stillsTable)
    .innerJoin(worksTable, eq(worksTable.id, stillsTable.workId))
    .where(and(...clauses))
    .orderBy(distance)
    .limit(n);
  return rows.map(({ row, work, distance: d }) => ({
    ...toStill(row),
    work: toWorkMeta(work),
    workLabel: toWorkMeta(work).label,
    similarity: 1 - Number(d),
  }));
}

// Work ids that fall inside a scope relative to one work: "work" (itself),
// "season" (same Shoko series), "franchise" (same top-level group; falls
// back to the season when the franchise was never synced), or null for all.
export async function workIdsInScope(workId, scope) {
  if (!scope || scope === "all") return null;
  if (scope === "work") return [workId];
  const [w] = await db().select().from(worksTable).where(eq(worksTable.id, workId));
  if (!w) return [workId];
  if (scope === "season") {
    const rows = await db().select({ id: worksTable.id }).from(worksTable).where(eq(worksTable.shokoSeriesId, w.shokoSeriesId));
    return rows.map((r) => r.id);
  }
  const [season] = await db().select().from(seasonsTable).where(eq(seasonsTable.id, w.shokoSeriesId));
  if (!season) return workIdsInScope(workId, "season");
  const rows = await db()
    .select({ id: worksTable.id })
    .from(worksTable)
    .innerJoin(seasonsTable, eq(seasonsTable.id, worksTable.shokoSeriesId))
    .where(eq(seasonsTable.franchiseId, season.franchiseId));
  return rows.map((r) => r.id);
}

export async function hasEmbedding(id) {
  const [row] = await db()
    .select({ has: isNotNull(stillsTable.embedding) })
    .from(stillsTable)
    .where(eq(stillsTable.id, id));
  return Boolean(row?.has);
}

// Review actions. state: "kept" | "culled" | "unreviewed". A note passed
// as undefined is left as it is (the workbench's quick actions); null
// clears it (the editor's empty field).
export async function setReview(id, state, note) {
  const set = { reviewState: state, reviewedAt: sql`now()` };
  if (note !== undefined) set.reviewNote = note;
  await db().update(stillsTable).set(set).where(eq(stillsTable.id, id));
}

// The workbench's bulk actions over a selection. Ids are checked against
// the work by the caller; notes are left alone.
export async function setReviewMany(ids, state) {
  if (!ids.length) return 0;
  const r = await db().execute(sql`update stills set review_state = ${state}, reviewed_at = now()
    where id in ${ids} returning id`);
  return r.rows.length;
}

// Locks go through pool.setLocks (the operator lock and the discard rule);
// this is for `excluded` only.
export async function setFlagMany(ids, flag, value) {
  if (!ids.length) return 0;
  if (flag !== "excluded") throw new Error("setFlagMany: locks go through setLocks");
  const r = await db().execute(sql`update stills set excluded = ${value} where id in ${ids} returning id`);
  return r.rows.length;
}

// A still as the public side may see it: picked, not culled, not hidden.
// The public still, similar and palette pages use this, so "hide from the
// public side" holds at the still's own address, not only in the grids.
export async function visibleStillById(id) {
  const [row] = await db().select().from(stillsTable).where(and(eq(stillsTable.id, id), visibleWhere()));
  return row ? toStill(row) : null;
}

// Owner corrections. facetsHuman: {family: value|null}; tagsHuman: {add, remove}.
// reviewed_at records the last time a person touched the still, so a bulk
// keep's undo leaves a still edited since alone.
export async function setCorrections(id, { facetsHuman, tagsHuman, note }) {
  await db()
    .update(stillsTable)
    .set({ facetsHuman, tagsHuman, reviewNote: note ?? null, reviewedAt: sql`now()` })
    .where(eq(stillsTable.id, id));
}

// Every distinct value seen per family, machine or human, for the editor's
// choices; the owner can also type a new value.
export async function facetChoices() {
  // Distinct family/value pairs in SQL, machine and human values alike
  // (a human null clears a value and is not a choice).
  const r = await db().execute(sql`select distinct key, value from (
      select f.key, f.value from stills, jsonb_each_text(facets) f
      union all
      select h.key, h.value from stills, jsonb_each_text(coalesce(facets_human, '{}'::jsonb)) h
    ) x where value is not null and value <> '' order by key, value`);
  const values = {};
  for (const row of r.rows) (values[row.key] ??= []).push(row.value);
  return values;
}

// The Review page's tree: the Library's grouping (franchise -> season ->
// episode), every level carrying the same review counts, so a new season is
// one row, not one row per episode. Counts are over picked stills;
// "unreviewed" leaves out hidden ones, as the workbench does.
export async function reviewTree() {
  const worksRows = await db().select().from(worksTable);
  const seasonRows = await db().select().from(seasonsTable);
  const franchiseRows = await db().select().from(franchisesTable);
  const countRows = await db()
    .select({
      workId: stillsTable.workId,
      picked: sql`count(*) filter (where ${stillsTable.selected})::int`,
      unreviewed: sql`count(*) filter (where ${stillsTable.selected} and ${stillsTable.reviewState} = 'unreviewed' and not ${stillsTable.excluded})::int`,
      kept: sql`count(*) filter (where ${stillsTable.selected} and ${stillsTable.reviewState} = 'kept')::int`,
      culled: sql`count(*) filter (where ${stillsTable.selected} and ${stillsTable.reviewState} = 'culled')::int`,
      hidden: sql`count(*) filter (where ${stillsTable.selected} and ${stillsTable.excluded})::int`,
      corrected: sql`count(*) filter (where ${stillsTable.selected} and (${stillsTable.facetsHuman} is not null or ${stillsTable.tagsHuman} is not null))::int`,
    })
    .from(stillsTable)
    .groupBy(stillsTable.workId);
  // Season-wide repeats still to review (what "Cull the repeats" takes).
  const repeats = await cullableRepeats();
  const counts = new Map(countRows.map((r) => [r.workId, { ...r, repeats: repeats.get(r.workId) ?? 0 }]));
  const ZERO = { picked: 0, unreviewed: 0, kept: 0, culled: 0, hidden: 0, corrected: 0, repeats: 0 };
  const sum = (items) => {
    const t = { ...ZERO, importedAt: null };
    for (const i of items) {
      for (const k of Object.keys(ZERO)) t[k] += i[k];
      if (i.importedAt && (!t.importedAt || i.importedAt > t.importedAt)) t.importedAt = i.importedAt;
    }
    return t;
  };
  const worksBySeries = new Map();
  for (const w of worksRows) {
    const c = counts.get(w.id);
    if (!c?.picked) continue;
    const list = worksBySeries.get(w.shokoSeriesId) ?? [];
    list.push({ ...toWorkMeta(w), ...c, importedAt: w.importedAt ? new Date(w.importedAt).toISOString() : null });
    worksBySeries.set(w.shokoSeriesId, list);
  }
  for (const list of worksBySeries.values()) list.sort(byEpisode);
  const seasonsByFranchise = new Map();
  for (const se of seasonRows) {
    const works = worksBySeries.get(se.id);
    if (!works) continue;
    const list = seasonsByFranchise.get(se.franchiseId) ?? [];
    list.push({ id: se.id, name: se.name, airDate: se.airDate, works, ...sum(works) });
    seasonsByFranchise.set(se.franchiseId, list);
  }
  const placed = new Set(seasonRows.map((se) => se.id));
  const franchises = [];
  for (const f of franchiseRows) {
    const seasons = (seasonsByFranchise.get(f.id) ?? []).sort((a, b) => (a.airDate ?? "9999").localeCompare(b.airDate ?? "9999") || a.name.localeCompare(b.name));
    if (!seasons.length) continue;
    franchises.push({ id: f.id, name: f.name, sortName: f.sortName ?? f.name, seasons, ...sum(seasons) });
  }
  const loose = [...worksBySeries.entries()].filter(([sid]) => !placed.has(sid)).flatMap(([, ws]) => ws);
  return { franchises, loose, totals: sum(franchises.concat(loose)) };
}

// The next still nobody has looked at, anywhere: newest import first, then
// in time order within the episode (the order a review pass wants).
export async function nextUnreviewedAnywhere() {
  const r = await db().execute(sql`select s.id, s.work_id from stills s join works w on w.id = s.work_id
    where s.selected and s.review_state = 'unreviewed' and not s.excluded
    order by w.imported_at desc, s.work_id, s.ts_seconds asc nulls last, s.id limit 1`);
  const row = r.rows[0];
  return row ? { id: row.id, work: await workMeta(row.work_id) } : null;
}

// The works a Review row covers: one episode, or every episode of a season.
export async function reviewScopeWorks(kind, id) {
  if (kind === "work") return (await workMeta(id)) ? [id] : [];
  if (kind === "season") {
    const rows = await db().select({ id: worksTable.id }).from(worksTable).where(eq(worksTable.shokoSeriesId, Number(id)));
    return rows.map((r) => r.id);
  }
  return [];
}

// Bulk accept (research/03 "bulk accept high-confidence results"): every
// picked, visible still in these works that nobody has reviewed becomes
// kept. Culled, kept, hidden, locked and corrected stills are not changed,
// and kept stills stay fully editable. Returns the count and the batch's
// timestamp, which undoKeep uses.
export async function keepRest(workIds) {
  if (!workIds.length) return { count: 0, at: null };
  const r = await db().execute(sql`update stills set review_state = 'kept', reviewed_at = now()
    where work_id in ${workIds} and selected and review_state = 'unreviewed' and not excluded
    returning reviewed_at::text as at`);
  // The exact stored text: a JS Date keeps milliseconds, Postgres keeps
  // microseconds, and undo matches on equality.
  return { count: r.rows.length, at: r.rows[0]?.at ?? null };
}

// Undoes one keepRest batch: only stills still kept at exactly that batch's
// time, so a still reviewed or edited since keeps what was done to it.
export async function undoKeep(workIds, at) {
  if (!workIds.length || !at) return 0;
  const r = await db().execute(sql`update stills set review_state = 'unreviewed', reviewed_at = null
    where work_id in ${workIds} and review_state = 'kept' and reviewed_at = ${at}::timestamptz
    returning id`);
  return r.rows.length;
}


// The other works of a work's Shoko series, for the workbench's repeat
// marks: how many there are and what to call each.
export async function workSiblings(workId) {
  const [w] = await db().select().from(worksTable).where(eq(worksTable.id, workId));
  if (!w || w.shokoSeriesId == null) return [];
  return (await db().select().from(worksTable).where(eq(worksTable.shokoSeriesId, w.shokoSeriesId)))
    .filter((r) => r.id !== workId)
    .map((row) => toWorkMeta(row))
    .sort(byEpisode)
    .map((m) => ({ id: m.id, name: m.episode ?? m.episodeTitle ?? m.title }));
}

// Admin overview: per work, how many stills are selected, unreviewed,
// culled, hidden, and carry corrections.
export async function reviewOverview() {
  const worksRows = await db().select().from(worksTable);
  const rows = await db()
    .select({
      workId: stillsTable.workId,
      selected: sql`count(*) filter (where ${stillsTable.selected})::int`,
      unreviewed: sql`count(*) filter (where ${stillsTable.selected} and ${stillsTable.reviewState} = 'unreviewed')::int`,
      culled: sql`count(*) filter (where ${stillsTable.reviewState} = 'culled')::int`,
      excluded: sql`count(*) filter (where ${stillsTable.excluded})::int`,
      corrected: sql`count(*) filter (where ${stillsTable.facetsHuman} is not null or ${stillsTable.tagsHuman} is not null)::int`,
    })
    .from(stillsTable)
    .groupBy(stillsTable.workId);
  const byWork = new Map(rows.map((r) => [r.workId, r]));
  return worksRows
    .map((w) => ({ ...toWorkMeta(w), ...(byWork.get(w.id) ?? { selected: 0, unreviewed: 0, culled: 0, excluded: 0, corrected: 0 }) }))
    .filter((w) => w.selected)
    .sort((a, b) => a.label.localeCompare(b.label));
}

// Returns false when a lock was refused (see pool.setLocks).
export async function setFlag(id, flag, value) {
  if (flag === "locked") {
    const { setLocks } = await import("./pool.js");
    return (await setLocks([id], value)).refused.length === 0;
  }
  await db().update(stillsTable).set({ excluded: value }).where(eq(stillsTable.id, id));
  return true;
}
