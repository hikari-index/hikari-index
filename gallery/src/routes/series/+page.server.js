import { library } from "$lib/server/catalog.js";

// The library, franchise-first (research/03: series, season, episode, movie
// are first-class hierarchy; the home page never lists episodes).
export async function load() {
  return await library();
}
