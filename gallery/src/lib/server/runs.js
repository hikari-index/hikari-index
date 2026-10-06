// The run records (the run folders under HIKARI_RUNS): where the gallery
// stores the results analyze workers send through the worker API, and what
// the import stage reads to put a work into the gallery.
//
//   runs/<work>/identity.json, extract/, extract.log      (source worker)
//   runs/<work>/analyze/                                   (stored here)
//   <images>/<work>/derivatives-manifest.json, <still>/…   (source worker)
//
// Records are kept permanently (ADR-0009): a second analyze result for the
// same work moves the first aside as analyze.superseded-<time>, it does not
// overwrite it.
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve, sep } from "node:path";
import { sql } from "drizzle-orm";
import { env } from "$env/dynamic/private";
import { db, schema } from "./db/index.js";
import { shoko, shokoConfigured } from "./shoko.js";

export const runsRoot = () => resolve(env.HIKARI_RUNS || "/runs");
const imagesRoot = () => resolve(env.HIKARI_IMAGES || "/images");
const stamp = () => new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");

export class ImportError extends Error {
  constructor(errorClass, message) {
    super(message);
    this.errorClass = errorClass;
  }
}

// files: [{ path: "tags/result-manifest.json", sha256, text }]. Every path
// stays inside analyze/; every file's checksum is checked before the set is
// put in place in one rename.
export function storeAnalyze(workId, jobId, files) {
  const workDir = join(runsRoot(), workId);
  if (!existsSync(join(workDir, "extract"))) throw new ImportError("input", `no extract record for ${workId}`);
  const staging = join(workDir, `.analyze-staging-${String(jobId).split("-")[0]}-${randomUUID().slice(0, 8)}`);
  mkdirSync(staging, { recursive: true });
  try {
    for (const f of files) {
      const rel = String(f.path || "");
      const target = resolve(staging, rel);
      if (!rel || rel.includes("\\") || !target.startsWith(staging + sep)) throw new ImportError("input", `bad result path ${rel}`);
      const got = createHash("sha256").update(f.text, "utf8").digest("hex");
      if (got !== f.sha256) throw new ImportError("transient", `checksum mismatch for ${rel}`);
      mkdirSync(dirname(target), { recursive: true });
      writeFileSync(target, f.text, "utf8");
    }
    const finalDir = join(workDir, "analyze");
    if (existsSync(finalDir)) renameSync(finalDir, join(workDir, `analyze.superseded-${stamp()}`));
    renameSync(staging, finalDir);
  } catch (error) {
    rmSync(staging, { recursive: true, force: true });
    throw error;
  }
  return `runs/${workId}/analyze`;
}

const readJson = (p) => JSON.parse(readFileSync(p, "utf8"));
const onlyDir = (d) => {
  const subs = readdirSync(d, { withFileTypes: true }).filter((e) => e.isDirectory());
  if (subs.length !== 1) throw new ImportError("input", `expected one folder under ${d}, found ${subs.length}`);
  return join(d, subs[0].name);
};

// The newest palette folder a `palette` stage wrote for the work
// (runs/<work>/palette/<provider version>/artifacts), if any: its
// descriptors win over the extraction's and the surplus lane's, per
// candidate, so a regeneration never touches the delivered results.
function paletteFolder(workDir) {
  const root = join(workDir, "palette");
  if (!existsSync(root)) return null;
  const versions = readdirSync(root, { withFileTypes: true })
    .filter((e) => e.isDirectory() && /^\d+\.\d+\.\d+$/.test(e.name) && existsSync(join(root, e.name, "artifacts")))
    .map((e) => e.name)
    .sort((a, b) => {
      const [x, y] = [a, b].map((v) => v.split(".").map(Number));
      return x[0] - y[0] || x[1] - y[1] || x[2] - y[2];
    });
  return versions.length ? join(root, versions[versions.length - 1], "artifacts") : null;
}

function readPalettes(dirs) {
  const palettes = new Map();
  for (const art of dirs) {
    if (!art || !existsSync(art)) continue;
    for (const name of readdirSync(art)) {
      if (!name.endsWith(".descriptor.json")) continue;
      const cid = name.split(".")[0];
      if (palettes.has(cid)) continue; // the first folder listed wins
      const pal = compactPalette(readJson(join(art, name)));
      if (pal) palettes.set(cid, pal);
    }
  }
  return palettes;
}

// The facets the gallery filters on, from a proposal's labels.
const FACET_PATHS = {
  setting: ["setting_time_weather", "setting"],
  time: ["setting_time_weather", "time"],
  weather: ["setting_time_weather", "weather"],
  shot_scale: ["shot_scale"],
  people: ["people"],
  color_bias: ["lighting_color_character", "color_bias"],
  saturation: ["lighting_color_character", "saturation"],
  lighting: ["lighting_color_character", "lighting"],
  composition: ["angle_composition", "composition"],
  angle: ["angle_composition", "angle"],
};
// Reasons to look at a still before the rest, from the evidence the run
// already holds. Measured 2026-09-30 over 57 works (4,558
// picked stills): text tags at the shipped 0.35 cut flag 4.6%, the
// tagger's questionable+explicit rating mass at 0.10 flags 4.2%, a scene
// label proposed under 0.35 (the 0.2653 band humans accepted ~90% of)
// flags 7.8%. "People but no face" would flag 32% and is left out: faces
// are a floor, not a disagreement.
const TEXT_TAGS = ["credits", "subtitled", "english_text", "text", "text_focus", "copyright_name", "artist_name",
  "character_name", "logo", "watermark", "signature", "chinese_text", "japanese_text", "dated", "title"];
const TEXT_CUT = 0.35;
const RATING_CUT = 0.1;
const UNSURE_CUT = 0.35;
export function reviewReasons(tagRecord, proposal) {
  const out = [];
  if (tagRecord) {
    const g = tagRecord.general_tags || {};
    const tags = TEXT_TAGS.filter((t) => (g[t] ?? 0) >= TEXT_CUT);
    if (tags.length) out.push({ k: "text", tags });
    const r = tagRecord.rating || {};
    const score = (r.questionable ?? 0) + (r.explicit ?? 0);
    if (score >= RATING_CUT) out.push({ k: "rating", score: Math.round(score * 100) / 100 });
  }
  for (const s of proposal?.proposal?.scores || []) {
    if (s.family !== "setting-time-weather" || s.label === "abstain" || !(s.score < UNSURE_CUT)) continue;
    out.push({ k: "unsure", label: s.label, score: Math.round(s.score * 100) / 100 });
  }
  return out;
}

// What made a run's labels, as the proposals file records it (a missing
// field is a run made before it was recorded).
function labelsRunOf(doc) {
  const first = doc.proposals?.[0]?.proposal ?? {};
  return {
    taxonomy: doc.taxonomy_version ?? null,
    allowlist: doc.allowlist_version ?? null,
    fusion: first.provider_version ?? null,
    cuts: doc.configuration ?? null,
  };
}

// Which signal proposed each facet, for the facets the run answered:
// {family: {s: source, p: score}}. Null for a run from before fusion
// recorded sources, except shot scale, whose lane was recorded first.
function facetSourcesOf(rec, facets) {
  const fields = rec?.provenance?.fields;
  const out = {};
  for (const name of Object.keys(facets)) {
    const f = fields?.[name];
    if (f && f.source && f.source !== "none") out[name] = { s: f.source, p: f.score ?? null };
    else if (name === "shot_scale" && rec?.provenance?.shot_scale_lane) out[name] = { s: rec.provenance.shot_scale_lane, p: null };
  }
  return Object.keys(out).length ? out : null;
}

const dig = (node, path) => {
  for (const k of path) node = node && typeof node === "object" ? node[k] : undefined;
  return typeof node === "string" ? node : null;
};

// What the gallery shows and filters on from a palette descriptor; the rest
// stays in the descriptor on the share.
function compactPalette(desc) {
  const pal = desc.palette || {};
  if (!pal.representative?.length) return null;
  const r = (x, n) => Math.round(x * 10 ** n) / 10 ** n;
  const sw = (items, n) => (items || []).slice(0, n).map((s) => ({ hex: s.hex, w: r(s.weight, 4), h: r(s.hue_deg, 1), s: r(s.sat, 3), l: r(s.luma, 3) }));
  const luma = desc.luma || {};
  // Up to six picks by area, then the accents (palette-descriptor 0.2.0:
  // a saturated colour spread too thin for the bin floor, unlike every
  // pick by area), flagged so the strip can show them as such.
  const rep = pal.representative;
  const swatches = [...sw(rep.filter((s) => !s.accent), 6), ...sw(rep.filter((s) => s.accent), 2).map((s) => ({ ...s, a: true }))];
  return {
    swatches,
    shadows: sw(pal.shadows, 2),
    midtones: sw(pal.midtones, 2),
    highlights: sw(pal.highlights, 2),
    hue_bands: ((desc.hue_family || {}).bands || []).map((x) => r(x, 4)),
    band_centers: (desc.hue_family || {}).band_centers_deg || [],
    luma: Object.fromEntries(["mean", "p05", "p50", "p95"].filter((k) => k in luma).map((k) => [k, r(luma[k], 4)])),
    sat: r((desc.chroma || {}).mean_sat_weighted ?? 0, 4),
    temp: Object.fromEntries(Object.entries(desc.temperature || {}).map(([k, v]) => [k, r(v, 4)])),
  };
}

function loadEmbeddings(analyzeDir) {
  const out = new Map();
  let model = null;
  for (const lane of ["embeddings", "surplus-embeddings"]) {
    const dir = join(analyzeDir, lane);
    if (!existsSync(join(dir, "artifacts"))) continue;
    try {
      const m = readJson(join(dir, "result-manifest.json"));
      model = model || m.processing_identity || m.processing?.identity || null;
    } catch {
      /* manifest optional for the vectors themselves */
    }
    for (const name of readdirSync(join(dir, "artifacts"))) {
      if (!name.endsWith(".embedding.json")) continue;
      const a = readJson(join(dir, "artifacts", name));
      out.set(a.candidate_id, a.embedding);
    }
  }
  return { vectors: out, model };
}

async function syncCatalogue(seriesId) {
  // A local series (ADR-0014): its rows were typed at onboarding and Shoko
  // has never heard of it.
  if (Number.isInteger(seriesId) && seriesId < 0) return "a local series (not from Shoko)";
  if (!shokoConfigured() || !Number.isInteger(seriesId)) return "skipped (Shoko not configured)";
  const doc = await (await shoko()).catalogue(seriesId);
  const { franchises, seasons } = schema;
  const f = doc.franchise;
  await db()
    .insert(franchises)
    .values({ id: f.shoko_group_id, name: f.name ?? String(f.shoko_group_id), sortName: f.sort_name, mainSeriesId: f.main_series_id })
    .onConflictDoUpdate({ target: franchises.id, set: { name: sql`excluded.name`, sortName: sql`excluded.sort_name`, mainSeriesId: sql`excluded.main_series_id`, syncedAt: sql`now()` } });
  for (const s of doc.seasons) {
    if (!Number.isInteger(s.shoko_series_id)) continue;
    await db()
      .insert(seasons)
      .values({ id: s.shoko_series_id, franchiseId: f.shoko_group_id, name: s.name ?? String(s.shoko_series_id), anidbType: s.anidb_type, airDate: s.air_date, endDate: s.end_date, localEpisodes: s.local_episodes })
      .onConflictDoUpdate({ target: seasons.id, set: { franchiseId: sql`excluded.franchise_id`, name: sql`excluded.name`, anidbType: sql`excluded.anidb_type`, airDate: sql`excluded.air_date`, endDate: sql`excluded.end_date`, localEpisodes: sql`excluded.local_episodes` } });
  }
  return `franchise ${f.shoko_group_id}, ${doc.seasons.length} seasons`;
}

// Put one work into the gallery from its run records. Idempotent, like the
// old import script: works and stills are upserted, and review state, locks,
// exclusions and corrections already in the database are never touched.
export async function importWork(workId) {
  const workDir = join(runsRoot(), workId);
  const extractDir = join(workDir, "extract");
  const analyzeDir = join(workDir, "analyze");
  const ladderDir = join(imagesRoot(), workId);
  const regenerated = paletteFolder(workDir);
  for (const [what, p] of [["extract record", extractDir], ["analyze record", analyzeDir], ["web images", join(ladderDir, "derivatives-manifest.json")]]) {
    if (!existsSync(p)) throw new ImportError("input", `no ${what} for ${workId} (${p})`);
  }
  const bundle = readJson(join(onlyDir(join(extractDir, "prepared")), "manifest.json"));
  const timing = new Map();
  for (const c of bundle.candidates) {
    const [num, den] = typeof c.time_base === "string" ? c.time_base.split("/") : c.time_base;
    timing.set(c.candidate_id, { ts: (c.intended_pts * Number(num)) / Number(den), shot: c.shot_id });
  }
  const surplusAudit = join(extractDir, "audit", "breadth-omitted-candidates.json");
  if (existsSync(surplusAudit)) {
    let rows = readJson(surplusAudit);
    rows = Array.isArray(rows) ? rows : Object.values(rows)[0];
    for (const r of rows) if (!timing.has(r.candidate_id)) timing.set(r.candidate_id, { ts: r.timestamp_seconds, shot: r.shot_id });
  }
  // Bundle frames are measured and labeled by extraction and the first
  // annotation pass; the surplus frames the picker took get the same after
  // the pick, under analyze/surplus-* (an analyze from before 2026-09-29 has
  // none, and those stills import bare, as they always did).
  const completed = join(extractDir, "results", "completed");
  const palettes = readPalettes([regenerated, existsSync(completed) ? join(onlyDir(completed), "artifacts") : null, join(analyzeDir, "surplus-colour", "artifacts")]);
  const proposals = new Map();
  let labelsRun = null;
  for (const file of ["proposals.json", "surplus-proposals.json"]) {
    if (!existsSync(join(analyzeDir, file))) continue;
    const doc = readJson(join(analyzeDir, file));
    for (const p of doc.proposals) proposals.set(p.candidate_id, p);
    labelsRun ??= labelsRunOf(doc);
  }
  // The tagger's raw record per frame (scores at the retention floor and
  // the segregated rating lane): evidence for the reasons, never a facet.
  const tagRecords = new Map();
  for (const sub of ["tags", "surplus-tags"]) {
    const p = join(analyzeDir, sub, "result-manifest.json");
    if (!existsSync(p)) continue;
    for (const c of readJson(p).candidates || []) tagRecords.set(c.candidate_id, c);
  }
  const selection = readJson(join(analyzeDir, "selection.json"));
  const picked = new Set(selection.selected || []);
  const { vectors, model } = loadEmbeddings(analyzeDir);
  const ladder = readJson(join(ladderDir, "derivatives-manifest.json"));
  const ident = identityOf(bundle.source_identity);

  const { works, stills } = schema;
  await db()
    .insert(works)
    .values({
      id: workId,
      title: ident.series_title ?? workId,
      entryType: ident.entry_type ?? "episode",
      episode: ident.episode ?? null,
      episodeTitle: ident.episode_title ?? null,
      shokoSeriesId: ident.series_id ?? null,
      shokoEpisodeIds: ident.episode_ids ?? null,
      shokoFileId: ident.file_id ?? null,
      bundleId: bundle.bundle_id ?? null,
      selection,
      labelsRun,
    })
    .onConflictDoUpdate({
      target: works.id,
      set: {
        title: sql`excluded.title`, entryType: sql`excluded.entry_type`, episode: sql`excluded.episode`,
        episodeTitle: sql`excluded.episode_title`, shokoSeriesId: sql`excluded.shoko_series_id`,
        shokoEpisodeIds: sql`excluded.shoko_episode_ids`, shokoFileId: sql`excluded.shoko_file_id`,
        bundleId: sql`excluded.bundle_id`, selection: sql`excluded.selection`, labelsRun: sql`excluded.labels_run`,
        // This import's stills have not been matched for repeats yet.
        repeatsKey: sql`null`,
      },
    });

  let n = 0;
  let withVector = 0;
  for (const entry of ladder.candidates) {
    const cid = entry.candidate_id;
    const t = timing.get(cid) || {};
    const rec = proposals.get(cid);
    const facets = {};
    let tags = [];
    if (rec) {
      const labels = rec.proposal.labels;
      for (const [name, path] of Object.entries(FACET_PATHS)) {
        const v = dig(labels, path);
        if (v && v !== "abstain") facets[name] = v;
      }
      const content = labels.content || {};
      tags = [...new Set([...(content.things || []), ...(content.actions || [])])].sort();
    }
    // v: the file's sha256 prefix, so its address changes when the file does (mediaUrl).
    const tiers = [...entry.tiers].sort((a, b) => a.width - b.width).map((x) => ({ w: x.width, h: x.height, src: `${workId}/${cid}/${x.file}`, v: x.sha256 ? x.sha256.slice(0, 12) : undefined }));
    const vector = vectors.get(cid) ?? null;
    if (vector) withVector += 1;
    const row = {
      id: `${workId}/${cid}`, workId, candidateId: cid, shotId: t.shot ?? rec?.shot_id ?? null, tsSeconds: t.ts ?? null,
      source: entry.source ?? "published", selected: picked.has(cid), facets, tags, tiers,
      palette: palettes.get(cid) ?? null, embedding: vector, embeddingModel: vector ? model : null,
      reviewReasons: reviewReasons(tagRecords.get(cid), rec), facetSources: facetSourcesOf(rec, facets),
    };
    await db()
      .insert(stills)
      .values(row)
      .onConflictDoUpdate({
        target: stills.id,
        // Pipeline columns follow the run; review columns are absent here
        // on purpose and survive a re-import.
        set: {
          shotId: sql`excluded.shot_id`, tsSeconds: sql`excluded.ts_seconds`, source: sql`excluded.source`,
          selected: sql`excluded.selected`, facets: sql`excluded.facets`, tags: sql`excluded.tags`,
          tiers: sql`excluded.tiers`, palette: sql`excluded.palette`, embedding: sql`excluded.embedding`,
          embeddingModel: sql`excluded.embedding_model`, reviewReasons: sql`excluded.review_reasons`,
          facetSources: sql`excluded.facet_sources`,
        },
      });
    n += 1;
  }
  // A re-run at another still count leaves stills the new set no longer
  // has, and derive has already replaced the work's images whole. One nobody
  // reviewed goes; one with a review mark, lock, exclusion or correction
  // stays as not picked (public pages show picked stills only), so no
  // decision is lost.
  const keep = ladder.candidates.map((c) => c.candidate_id);
  const dropped = await db().execute(sql`delete from stills
    where work_id = ${workId} and not (candidate_id = any(${`{${keep.join(",")}}`}::text[]))
      and review_state = 'unreviewed' and not locked and not excluded
      and facets_human is null and tags_human is null
    returning id`);
  // Derive replaced the work's image folder with the new set's, so a
  // dropped still's web images are gone: its tiers go with them (an empty
  // tiers list is how pages and the lock rule know a still has none).
  const hidden = await db().execute(sql`update stills set selected = false, tiers = '[]'::jsonb
    where work_id = ${workId} and not (candidate_id = any(${`{${keep.join(",")}}`}::text[])) and selected
    returning id`);
  await db().execute(sql`update stills set tiers = '[]'::jsonb
    where work_id = ${workId} and not (candidate_id = any(${`{${keep.join(",")}}`}::text[]))
      and not selected and jsonb_array_length(tiers) > 0`);
  let catalogue;
  try {
    catalogue = await syncCatalogue(ident.series_id);
  } catch (error) {
    catalogue = `not synced: ${error.message}`; // the work is in; the Library grouping can follow later
  }
  const inLadder = (k) => ladder.candidates.some((c) => c.candidate_id === k);
  return {
    work: workId, stills: n, selected: [...picked].length, with_vector: withVector,
    with_palette: [...palettes.keys()].filter(inLadder).length,
    with_labels: [...proposals.keys()].filter(inLadder).length,
    dropped: dropped.rows.length, reviewed_not_picked: hidden.rows.length, catalogue,
  };
}

// A bundle's source identity with the format 2.4 key names. Bundles made
// before 2.4 (every run folder up to 2026-10-03) name the id fields after
// Shoko; they are read for as long as they exist, so the old names are
// mapped here and nowhere else.
export function identityOf(identity) {
  const i = identity || {};
  return {
    ...i,
    series_id: i.series_id ?? i.shoko_series_id ?? null,
    episode_ids: i.episode_ids ?? i.shoko_episode_ids ?? null,
    file_id: i.file_id ?? i.shoko_file_id ?? null,
    work_id: i.work_id ?? i.fixture_id ?? null,
  };
}
