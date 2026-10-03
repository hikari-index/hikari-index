// Technique index (research/03 "Composition atlas", reshaped after what
// reference libraries do — research note 08): one tile per named value
// the taxonomy emits, with a three-still mosaic and a count. A tile links
// into Explore with that facet set, so there is one results surface and
// one way to narrow. No matrix: people look for "low angle" or
// "silhouette" by name.

// Families shown, in display order, with a short human heading.
export const FAMILIES = [
  ["shot_scale", "Shot scale"],
  ["angle", "Camera angle"],
  ["composition", "Composition"],
  ["people", "People"],
  ["lighting", "Lighting"],
  ["time", "Time of day"],
  ["weather", "Weather"],
  ["setting", "Setting"],
];

export function techniques(stills) {
  const groups = new Map();
  for (const s of stills) {
    for (const [family] of FAMILIES) {
      const value = s.facets[family];
      if (!value) continue;
      const key = `${family}:${value}`;
      let g = groups.get(key);
      if (!g) groups.set(key, (g = { family, value, count: 0, covers: [] }));
      g.count += 1;
      // Spread the mosaic across works so a tile is not three frames of one episode.
      if (g.covers.length < 3 && !g.covers.some((c) => c.workId === s.workId)) g.covers.push(s);
    }
  }
  const out = FAMILIES.map(([family, label]) => ({
    family,
    label,
    tiles: [...groups.values()]
      .filter((g) => g.family === family && g.count >= 5 && !["none-visible", "unknown", "mixed"].includes(g.value))
      .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value)),
  })).filter((f) => f.tiles.length);
  return out;
}
