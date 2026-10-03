// The Onboard picker's rules (research/01 "Shoko entity boundary",
// "Identity rules", "Probe manifest"): turn Shoko's Series / Episode / File
// records into choices, and refuse the cases a human has to settle rather
// than guessing. The chosen File is the source; every Episode it
// cross-references is kept.

import { shownEpisodeTitle } from "$lib/titles.js";

// Strongest hash Shoko holds for a file. Which hashes it computes is an
// installation setting: files imported since 2026-07 carry only ED2K and
// CRC32 on this server.
export function fingerprint(file) {
  const hashes = Object.fromEntries((file.Hashes || []).map((h) => [h.Type, h.Value]));
  for (const t of ["SHA1", "ED2K", "MD5", "CRC32"]) if (hashes[t]) return `${t.toLowerCase()}:${hashes[t]}`;
  return null;
}

export function parseDuration(value) {
  if (value == null || value === "") return null;
  const text = String(value);
  if (!text.includes(":")) return Math.round(Number(text) / 100) / 10;
  const [h, m, s] = text.split(":");
  return Math.round((Number(h) * 3600 + Number(m) * 60 + Number(s)) * 10) / 10;
}

export function mediaOf(file) {
  const video = file.MediaInfo?.Video || [];
  const v = video[0] || {};
  return {
    video_streams: video.length,
    codec: v.Codec?.Simplified ?? v.Codec ?? null,
    width: v.Width ?? null,
    height: v.Height ?? null,
    bit_depth: v.BitDepth ?? null,
    frame_rate: v.FrameRate ?? null,
    duration_seconds: parseDuration(file.MediaInfo?.Duration ?? file.Duration),
  };
}

// Problems that stop an onboarding (blockers) and ones the operator should
// see before confirming (notes). `detail` is GET /File/{id} with XRefs; the
// episode list's embedded files lack cross-references, so the list shows a
// first pass and the confirm page re-reads the file for the final say.
export function fileProblems(file, detail = null) {
  const blockers = [];
  const notes = [];
  if (file.IsIgnored) blockers.push("Shoko marks this file ignored.");
  if (!fingerprint(file)) blockers.push("Shoko holds no hash for this file, so its identity cannot be checked before a run.");
  const locations = (file.Locations || []).filter((l) => l.IsAccessible ?? l.Accessible);
  if (!locations.length) blockers.push("Shoko's record of a file that is no longer on disk (its path is gone; Shoko's own pages hide these). Pick the other file, if the episode has one.");
  const media = mediaOf(file);
  if (media.video_streams > 1) blockers.push(`The file has ${media.video_streams} video streams; choosing one is a human call.`);
  if (media.video_streams === 0 && file.MediaInfo) blockers.push("Shoko's media info lists no video stream.");
  if (file.IsVariation) notes.push("Shoko marks this file as a variation (an alternate release).");
  if (detail) {
    const series = detail.SeriesIDs || [];
    if (series.length !== 1) blockers.push(`The file maps to ${series.length} series; that needs a human, not a guess.`);
    const episodes = series.flatMap((s) => s.EpisodeIDs || []);
    if (!episodes.length) blockers.push("The file has no episode mapping in Shoko.");
    const partial = episodes.filter((e) => e.Percentage && (e.Percentage.Start !== 0 || e.Percentage.End !== 100));
    if (partial.length) blockers.push("The file covers only part of an episode (a multipart release); that is outside what one run can handle.");
    if (episodes.length > 1) notes.push(`The file covers ${episodes.length} episodes; all of them are kept on the record.`);
  }
  return { blockers, notes };
}

// The pipeline's --source-identity object: exactly the keys the bundle
// format allows (pipeline_cli IDENTITY_REQUIRED / _OPTIONAL). A stray key
// fails the bundle after the whole extraction, so nothing else goes in.
export function identityFor({ series, episode, detail, workId }) {
  const xref = detail.SeriesIDs[0];
  const location = (detail.Locations || []).find((l) => l.IsAccessible ?? l.Accessible) || detail.Locations[0];
  const relative = String(location.RelativePath || "").replace(/^[\\/]+/, "");
  const entryType = series.AniDB?.Type === "Movie" ? "movie" : "episode";
  const identity = {
    series_title: series.Name,
    entry_type: entryType,
    source_filename: relative.split(/[\\/]/).pop(),
    series_id: xref.SeriesID.ID,
    episode_ids: (xref.EpisodeIDs || []).map((e) => e.ID),
    file_id: detail.ID,
    work_id: workId,
  };
  if (episode?.AniDB?.EpisodeNumber != null) identity.episode = episode.AniDB.EpisodeNumber;
  if (episode?.Name) identity.episode_title = episode.Name;
  return identity;
}

// Where the file is, as Shoko sees it: a managed folder and a path inside
// it. Each worker maps the folder to its own mount; that mapping is worker
// configuration, not part of the job (research/01 "Path translation").
export function sourceFor(detail) {
  return {
    fingerprint: fingerprint(detail),
    size: detail.Size,
    locations: (detail.Locations || []).map((l) => ({
      managed_folder_id: l.ManagedFolderID ?? l.ImportFolderID ?? null,
      relative_path: l.RelativePath,
      accessible: l.IsAccessible ?? l.Accessible ?? null,
    })),
    media: mediaOf(detail),
  };
}

// The work id: the one name a work has in addresses, image folders and run
// folders, so it must read as the work, not as a pipeline code. The whole
// Shoko series title (a leading "The" dropped, apostrophes dropped, other
// punctuation to dashes, cut at a word boundary past 60 characters) plus
// -eNN for an episode. Two Shoko series that differ only by a year or a
// subtitle keep that difference. The operator can change it on the
// confirm page; a taken id is refused there.
function slugOf(text) {
  return String(text || "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/['\u2019]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// A film's id is its series name; a film that is one part of several
// (one AniDB entry, several parts) adds the part's name,
// so the parts do not collide. One-part films carry AniDB's placeholder
// "Complete Movie", which adds nothing.
export function defaultWorkId(seriesName, entryType, episodeNumber, episodeTitle = null) {
  let base = String(seriesName || "work")
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/['’]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/^the-/, "");
  if (base.length > 60) base = base.slice(0, 61).replace(/-[^-]*$/, "");
  base ||= "work";
  if (entryType === "episode" && episodeNumber != null) base += `-e${String(episodeNumber).padStart(2, "0")}`;
  const part = entryType === "movie" ? shownEpisodeTitle(entryType, episodeTitle) : null;
  if (part) {
    // The part is what tells the parts apart, so it keeps its place. A
    // part name with nothing to slug (kana, kanji) falls back to its
    // number. When series and part together run past 72, the series name
    // gives way first (cutting the whole id at 72 could drop the part),
    // shortened to around 24 characters at most; a part name that still must be
    // cut takes its number, so two long part names are not cut to the
    // same id. This is a default: what it cannot tell apart (a cut part
    // name with no number) the confirm page refuses as a duplicate, and
    // the operator types the id.
    let suffix = slugOf(part);
    if (!suffix && episodeNumber != null) suffix = `part-${episodeNumber}`;
    if (suffix) {
      const MAX = 72;
      if (base.length + 1 + suffix.length > MAX) {
        base = cutAtWord(base, Math.max(24, MAX - 1 - suffix.length), 12);
        if (base.length + 1 + suffix.length > MAX) {
          const number = episodeNumber != null ? `-${episodeNumber}` : "";
          suffix = cutAtWord(suffix, MAX - 1 - base.length - number.length) + number;
        }
      }
      base = `${base}-${suffix}`;
    }
  }
  return base;
}

// A slug shortened to at most `max` characters, at a word boundary when
// that keeps at least `floor` of them, else cut mid-word.
function cutAtWord(slug, max, floor = 1) {
  if (slug.length <= max) return slug;
  const cut = slug.slice(0, max + 1);
  const atWord = cut.includes("-") ? cut.replace(/-[^-]*$/, "") : "";
  return (atWord.length >= Math.min(floor, max) ? atWord : cut.slice(0, max)).replace(/-+$/, "");
}

export const WORK_ID = /^[a-z0-9][a-z0-9-]{1,71}$/;

// research/01 "Expose fewer / balanced / more". Balanced is the pipeline's
// own count (0.15 per detected shot, 35 to 300); the others scale it at the
// pick, which chooses from the whole extracted pool, so the choice costs no
// extra decoding. The analyze worker applies the factor (hikari_worker/analyze.py).
export const BUDGETS = {
  fewer: { factor: 0.6, label: "Fewer", note: "about 60% of balanced" },
  balanced: { factor: 1, label: "Balanced", note: "worked out from the shot count, as for every work so far" },
  more: { factor: 1.5, label: "More", note: "about 150% of balanced, at most 300" },
};

// At most this many files in one confirm page: each one is re-read from
// Shoko on load and again on submit.
export const BATCH_MAX = 60;
