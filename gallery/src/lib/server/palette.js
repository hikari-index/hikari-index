// Palette browsing read model. Two ideas, both from what reference sites
// do and from research/03's "select one or several swatches, strict versus
// broad matching":
//
//   cloud(stills)          the library's colors as real swatches with counts
//   pick(stills, colors)  stills whose palette contains each chosen color
//
// Color distance is OKLab (src/lib/color.js). The cloud is built by
// quantising every representative swatch into OKLab cells, so shades that
// a person would call the same color merge into one chip whose color is
// the weighted mean of its members. Explainable, no learned metric.
import { colorName, deltaE, hexToOklab, oklabToRgb, rgbToHex } from "$lib/color.js";

// Cell size in OKLab units per axis. 0.09 on L and 0.05 on a/b gives a few
// hundred chips for a few thousand stills; tune by eye, not by metric.
const CELL = [0.09, 0.05, 0.05];

export const MATCH = {
  strict: 0.045,
  broad: 0.09,
};

function cellKey(lab) {
  return lab.map((v, i) => Math.round(v / CELL[i])).join(",");
}

export function cloud(stills, { minStills = 3 } = {}) {
  const cells = new Map();
  for (const s of stills) {
    for (const sw of s.palette?.swatches ?? []) {
      const lab = hexToOklab(sw.hex);
      const key = cellKey(lab);
      let cell = cells.get(key);
      if (!cell) cells.set(key, (cell = { sum: [0, 0, 0], weight: 0, stills: new Set() }));
      const w = sw.w || 0.01;
      cell.sum = cell.sum.map((v, i) => v + lab[i] * w);
      cell.weight += w;
      cell.stills.add(s.id);
    }
  }
  const chips = [];
  for (const cell of cells.values()) {
    if (cell.stills.size < minStills) continue;
    const lab = cell.sum.map((v) => v / cell.weight);
    const chroma = Math.hypot(lab[1], lab[2]);
    const hue = ((Math.atan2(lab[2], lab[1]) * 180) / Math.PI + 360) % 360;
    chips.push({ hex: rgbToHex(oklabToRgb(lab)), count: cell.stills.size, weight: cell.weight, lab, chroma, hue, neutral: chroma < 0.035 });
  }
  // Label each chip with the number of stills a strict pick of it returns,
  // not the cell membership: the promise and the result must agree.
  const labs = stills.map((s) => ({ id: s.id, labs: (s.palette?.swatches ?? []).map((sw) => hexToOklab(sw.hex)) }));
  for (const chip of chips) {
    const target = hexToOklab(chip.hex); // the rounded color the click will actually use
    let n = 0;
    for (const s of labs) if (s.labs.some((l) => deltaE(l, target) <= MATCH.strict)) n += 1;
    chip.count = n;
    chip.name = colorName(chip.lab);
  }
  // Keep the cloud readable: the sixty best-supported colors, then the
  // caller orders them by color or count. Each color once: two cells can
  // round to the same hex (their counts are then equal), and the page keys
  // the cloud by hex.
  const seen = new Set();
  return chips.filter((c) => c.count >= minStills && !seen.has(c.hex) && seen.add(c.hex)).sort((a, b) => b.count - a.count).slice(0, 60);
}

// "color": neutrals first (dark to light), then chromatic swatches around
// the hue circle, lighter within a hue. "count": most stills first.
export function orderChips(chips, order = "color") {
  const list = [...chips];
  if (order === "count") return list.sort((a, b) => b.count - a.count || b.weight - a.weight);
  return list.sort((a, b) => {
    if (a.neutral !== b.neutral) return a.neutral ? -1 : 1;
    if (a.neutral) return a.lab[0] - b.lab[0];
    const ha = Math.round(a.hue / 20), hb = Math.round(b.hue / 20);
    return ha - hb || a.lab[0] - b.lab[0];
  });
}

// Every still whose palette has, for EACH chosen color, a swatch within
// the match distance; ranked by how much of the frame those swatches
// cover (sum of matched weights) and how close they are.
export function pick(stills, hexes, mode = "strict") {
  const limit = MATCH[mode] ?? MATCH.strict;
  const targets = hexes.map(hexToOklab);
  const out = [];
  for (const s of stills) {
    const swatches = s.palette?.swatches ?? [];
    if (!swatches.length) continue;
    let score = 0;
    let ok = true;
    for (const t of targets) {
      let best = null;
      for (const sw of swatches) {
        const d = deltaE(t, hexToOklab(sw.hex));
        if (d <= limit && (best == null || d < best.d)) best = { d, w: sw.w };
      }
      if (!best) {
        ok = false;
        break;
      }
      score += best.w * (1 - best.d / limit);
    }
    if (ok) out.push({ ...s, score });
  }
  out.sort((a, b) => b.score - a.score);
  return out;
}

// Nearest palettes to a still (kept from the first atlas): hue-band mass
// distance plus luma and saturation distance. Explainable, not learned.
export function nearest(target, stills, n = 24) {
  const t = target.palette;
  if (!t) return [];
  const scored = [];
  for (const s of stills) {
    if (s.id === target.id || !s.palette) continue;
    const p = s.palette;
    let hue = 0;
    for (let i = 0; i < t.hue_bands.length; i++) {
      hue += Math.abs((t.hue_bands[i] ?? 0) - (p.hue_bands[i] ?? 0));
    }
    const luma = Math.abs((t.luma.p50 ?? 0) - (p.luma.p50 ?? 0));
    const sat = Math.abs((t.sat ?? 0) - (p.sat ?? 0));
    scored.push({ still: s, distance: hue + luma + sat });
  }
  scored.sort((a, b) => a.distance - b.distance);
  return scored.slice(0, n);
}


// ---------------------------------------------------------------------------
// Grades: browse by what happens in the darks and the lights (research/03's
// "shadow/highlight relationship"; UX review 2026-09-26, design 1). Every
// still carries shadow, midtone and highlight swatches; a grade is a
// shadow color paired with a highlight color. Nobody else's palette data
// can answer "cool shadows, warm highlights", so this is the page's own idea.

const GRADE_RADIUS = 0.07;

// A plain-English temperature name for a color, from its OKLab hue angle.
export function toneName(lab) {
  const chroma = Math.hypot(lab[1], lab[2]);
  if (chroma < 0.03) return "neutral";
  const hue = ((Math.atan2(lab[2], lab[1]) * 180) / Math.PI + 360) % 360;
  if (hue >= 15 && hue < 110) return "warm";   // reds through yellows
  if (hue >= 110 && hue < 175) return "green";
  if (hue >= 175 && hue < 300) return "cool";  // cyans through blues
  return "magenta";                            // violets through pinks
}

// A lane's swatches are always merged over the whole library, so a swatch
// is the same color on every page and a chosen one can be found and marked
// in its row. `pool` (the stills the other lane's choice leaves) only
// counts them; `keep` is the chosen swatch, shown whatever its count.
function mergeSwatches(stills, lane, { minStills = 3, pool = stills, keep = null } = {}) {
  const cells = new Map();
  for (const s of stills) {
    for (const sw of s.palette?.[lane] ?? []) {
      const lab = hexToOklab(sw.hex);
      const key = cellKey(lab);
      let cell = cells.get(key);
      if (!cell) cells.set(key, (cell = { sum: [0, 0, 0], weight: 0, stills: new Set() }));
      const w = sw.w || 0.01;
      cell.sum = cell.sum.map((v, i) => v + lab[i] * w);
      cell.weight += w;
      cell.stills.add(s.id);
    }
  }
  // The colors to offer: each well-supported cell's mean, once (two cells
  // can round to the same hex, and the page keys a row by hex), plus the
  // chosen one when the library no longer merges to exactly it (stills
  // added since the click, or an address typed by hand).
  const colors = new Map();
  for (const cell of cells.values()) {
    if (cell.stills.size < minStills) continue;
    const lab = cell.sum.map((v) => v / cell.weight);
    const hex = rgbToHex(oklabToRgb(lab));
    if (!colors.has(hex)) colors.set(hex, lab);
  }
  if (keep && !colors.has(keep)) colors.set(keep, hexToOklab(keep));
  const chips = [];
  for (const [hex, lab] of colors) {
    const target = hexToOklab(hex);
    // The count is what a click returns (gradePick's rule over the pool),
    // not how many stills fell in the cell that made the swatch.
    let count = 0;
    for (const s of pool) if (within(lane, s, target)) count += 1;
    if (count < minStills && hex !== keep) continue;
    chips.push({ hex, lab: target, count, tone: toneName(lab), hue: ((Math.atan2(lab[2], lab[1]) * 180) / Math.PI + 360) % 360, neutral: Math.hypot(lab[1], lab[2]) < 0.03 });
  }
  return orderChips(chips, "color");
}

function within(lane, s, target) {
  let best = null;
  for (const sw of s.palette?.[lane] ?? []) {
    const d = deltaE(hexToOklab(sw.hex), target);
    if (d <= GRADE_RADIUS && (best == null || d < best.d)) best = { d, w: sw.w };
  }
  return best;
}

// The two swatch rows, plus the most common (shadow tone, highlight tone)
// pairs as tiles. Tiles use the tone names so they read as sentences.
export function grades(stills, { tiles = 9, shadowPool = stills, highlightPool = stills, shadow = null, highlight = null } = {}) {
  const shadows = mergeSwatches(stills, "shadows", { pool: shadowPool, keep: shadow });
  const highlights = mergeSwatches(stills, "highlights", { pool: highlightPool, keep: highlight });
  const pairs = new Map();
  for (const s of stills) {
    const sh = s.palette?.shadows?.[0];
    const hi = s.palette?.highlights?.[0];
    if (!sh || !hi) continue;
    const a = toneName(hexToOklab(sh.hex));
    const b = toneName(hexToOklab(hi.hex));
    const key = `${a}|${b}`;
    let pair = pairs.get(key);
    if (!pair) pairs.set(key, (pair = { shadowTone: a, highlightTone: b, count: 0, covers: [], sh: [0, 0, 0], hi: [0, 0, 0] }));
    pair.count += 1;
    const la = hexToOklab(sh.hex), lb = hexToOklab(hi.hex);
    pair.sh = pair.sh.map((v, i) => v + la[i]);
    pair.hi = pair.hi.map((v, i) => v + lb[i]);
    if (pair.covers.length < 3 && !pair.covers.some((c) => c.workId === s.workId)) pair.covers.push(s);
  }
  const common = [...pairs.values()]
    .map((p) => ({
      label: `${p.shadowTone} shadows, ${p.highlightTone} highlights`,
      shadowTone: p.shadowTone,
      highlightTone: p.highlightTone,
      count: p.count,
      covers: p.covers,
      shadowHex: rgbToHex(oklabToRgb(p.sh.map((v) => v / p.count))),
      highlightHex: rgbToHex(oklabToRgb(p.hi.map((v) => v / p.count))),
    }))
    .sort((a, b) => b.count - a.count)
    .slice(0, tiles);
  return { shadows, highlights, common };
}

// The color a tone choice ("cool" shadows, from a common-grade tile) stands
// for on its page: the mean of the lane's top swatch over the stills shown,
// as the tile's own pair is the mean over its stills. Null with no stills.
export function toneSwatch(stills, lane) {
  const sum = [0, 0, 0];
  let n = 0;
  for (const s of stills) {
    const top = s.palette?.[lane]?.[0];
    if (!top) continue;
    const lab = hexToOklab(top.hex);
    for (let i = 0; i < 3; i++) sum[i] += lab[i];
    n += 1;
  }
  return n ? rgbToHex(oklabToRgb(sum.map((v) => v / n))) : null;
}

// Stills matching a chosen shadow color and/or highlight color (either
// may be absent), ranked by coverage and closeness. Tone names may be
// given instead of colors ("cool" shadows) and match by name.
export function gradePick(stills, { shadow, highlight, shadowTone, highlightTone } = {}) {
  const sh = shadow ? hexToOklab(shadow) : null;
  const hi = highlight ? hexToOklab(highlight) : null;
  const out = [];
  for (const s of stills) {
    if (!s.palette) continue;
    let score = 0;
    if (sh) {
      const m = within("shadows", s, sh);
      if (!m) continue;
      score += m.w * (1 - m.d / GRADE_RADIUS);
    } else if (shadowTone) {
      const top = s.palette.shadows?.[0];
      if (!top || toneName(hexToOklab(top.hex)) !== shadowTone) continue;
      score += top.w;
    }
    if (hi) {
      const m = within("highlights", s, hi);
      if (!m) continue;
      score += m.w * (1 - m.d / GRADE_RADIUS);
    } else if (highlightTone) {
      const top = s.palette.highlights?.[0];
      if (!top || toneName(hexToOklab(top.hex)) !== highlightTone) continue;
      score += top.w;
    }
    out.push({ ...s, score });
  }
  out.sort((a, b) => b.score - a.score);
  return out;
}
