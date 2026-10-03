// What an episode title shows as. AniDB names the single part of a
// one-part film "Complete Movie"; Shoko passes it on, and as a label it is
// noise ("A Film · Complete Movie"), so a film's title is shown without
// it. A multi-part film's parts keep their names (a trilogy can be one
// AniDB entry with three parts).
export const PLACEHOLDER_FILM_TITLES = new Set(["complete movie"]);

export function shownEpisodeTitle(entryType, title) {
  if (!title) return null;
  if (entryType === "movie" && PLACEHOLDER_FILM_TITLES.has(String(title).trim().toLowerCase())) return null;
  return title;
}
