// Reading an episode number out of a file name, for the "add a folder"
// page. A guess, shown in an editable field: the operator is the authority,
// this only saves typing. No library's naming is assumed; the patterns are
// the ones release groups and media managers actually use.

// Bracketed and parenthesised runs are group tags, hashes and quality
// notes ("[Group]", "(1080p)", "[A1B2C3D4]"), never the episode number.
const stripTags = (stem) => stem.replace(/\[[^\]]*\]|\([^)]*\)/g, " ").replace(/\s+/g, " ").trim();

const stemOf = (name) => String(name ?? "").replace(/\.[^.]+$/, "");

// The episode number a file name suggests, or null. In order of trust:
// S01E07-style, "- 07" after the title, "E07" / "Ep07" / "Episode 07", a
// bare "07" that is the only number left once tags are gone.
export function guessEpisode(name) {
  const stem = stripTags(stemOf(name));
  const tries = [
    /\bS\d{1,2}\s?E(\d{1,4})\b/i,
    /\s[-–—]\s*(\d{1,4})(?:v\d)?(?=\s|$|[-–—.])/,
    /\b(?:E|Ep\.?|Episode)\s?(\d{1,4})\b/i,
    /(?:^|\s)#(\d{1,4})\b/,
  ];
  for (const re of tries) {
    const m = stem.match(re);
    if (m) return Number(m[1]);
  }
  // Not a number glued to a unit ("4K", "1080p", "10bit", "x265").
  const bare = [...stem.matchAll(/(?<![\d.x])(\d{1,4})(?![\d.]|\s?(?:k|p|bit|fps|hz|x)\b)/gi)].map((m) => Number(m[1]));
  // One number alone is probably the episode; several (a year, a
  // resolution, a season) are a guess not worth making.
  return bare.length === 1 ? bare[0] : null;
}

// Files a season folder often holds that are not episodes: creditless
// openings and endings, previews, menus, extras. Left unticked by default.
export function looksLikeExtra(name) {
  // The marker is as often inside the brackets ("- 00 [NCOP]") as outside,
  // so the whole stem is read, tags included.
  const stem = stemOf(name).replace(/[[\]()]/g, " ");
  return /\b(NC(OP|ED)\d*|OP\d*|ED\d*|PV\d*|CM\d*|Preview|Trailer|Menu|Extras?|Bonus|Special|OVA|OAD|Recap|Omake|Creditless|Clean (Opening|Ending)|Textless)\b/i.test(stem);
}

// Natural order ("Episode 2" before "Episode 10").
const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });
export const naturalCompare = (a, b) => collator.compare(a, b);
