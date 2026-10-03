// Shared (server and browser): when a repeat counts as season-wide.
//
// A frame "repeats" when it sits in a run of frames another work of the
// same Shoko series also has, in step (lib/server/repeats.js, ADR-0012).
// Season-wide is what an opening, an ending or a studio logo looks like:
// the frame recurs in at least half of the season's other works, and in at
// least two, so a pair of episodes that share a recap is never enough. The
// Review page's "Cull the repeats" acts on season-wide repeats only; the
// same rule is written in SQL in catalog.js (SEASON_WIDE).
export const seasonWide = (n, others) => n >= Math.max(2, Math.ceil(others / 2));
