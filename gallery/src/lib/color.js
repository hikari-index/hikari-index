// Small color maths shared by server and browser: sRGB hex → OKLab, a
// perceptual distance, and a readable text color for a swatch chip.
// OKLab (Björn Ottosson, 2020) is used because equal distances read as
// roughly equal color differences, which is what "similar color" means
// to a person; the constants are the published ones.

export function hexToRgb(hex) {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255);
}

export function rgbToHex([r, g, b]) {
  return "#" + [r, g, b].map((v) => Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16).padStart(2, "0")).join("").toUpperCase();
}

function toLinear(c) {
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}
function fromLinear(c) {
  return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
}

export function rgbToOklab([r, g, b]) {
  const [lr, lg, lb] = [r, g, b].map(toLinear);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

export function oklabToRgb([L, a, b]) {
  const l = Math.pow(L + 0.3963377774 * a + 0.2158037573 * b, 3);
  const m = Math.pow(L - 0.1055613458 * a - 0.0638541728 * b, 3);
  const s = Math.pow(L - 0.0894841775 * a - 1.291485548 * b, 3);
  return [
    fromLinear(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    fromLinear(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    fromLinear(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

export const hexToOklab = (hex) => rgbToOklab(hexToRgb(hex));

// Euclidean distance in OKLab. Rough scale: 0.02 is barely visible,
// 0.1 is clearly a different shade of the same color, 0.25 a different color.
export function deltaE(a, b) {
  return Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
}

// WCAG 2 relative luminance and contrast ratio, for the two places the
// gallery has to decide whether a color is visible against another.
export function luminance(hex) {
  const [r, g, b] = hexToRgb(hex).map(toLinear);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// Black or white text over a swatch: whichever contrasts more. Mid-tones
// give neither side 4.5:1; callers keep the hex available on hover.
export function textOn(hex) {
  return contrast(hex, "#111111") >= contrast(hex, "#f4f4f4") ? "#111" : "#f4f4f4";
}

// A swatch within 1.5:1 of the surface it sits on would vanish into it
// (a near-black shadow swatch on the near-black page); it gets a hairline.
export const PAGE_BG = "#0b0b0d";
export function needsKeyline(hex, bg = PAGE_BG) {
  return contrast(hex, bg) < 1.5;
}


// A plain name for a color, from OKLab: lightness word + hue word, with
// "gray" for low chroma. Approximate on purpose; it is a label, not a
// measurement, and the hex stays available on hover.
export function colorName(lab) {
  const [L, a, b] = lab;
  const chroma = Math.hypot(a, b);
  const light = L < 0.25 ? "very dark" : L < 0.4 ? "dark" : L < 0.6 ? "" : L < 0.8 ? "light" : "pale";
  if (chroma < 0.03) {
    if (L < 0.2) return "black";
    if (L > 0.9) return "white";
    return `${light} gray`.trim();
  }
  const hue = ((Math.atan2(b, a) * 180) / Math.PI + 360) % 360;
  const name =
    hue < 20 ? "pink" : hue < 45 ? "red" : hue < 70 ? "orange" : hue < 95 ? "amber" : hue < 115 ? "yellow" : hue < 160 ? "green"
    : hue < 195 ? "teal" : hue < 235 ? "cyan" : hue < 275 ? "blue" : hue < 310 ? "violet" : hue < 340 ? "magenta" : "pink";
  const muted = chroma < 0.07 ? "muted " : "";
  return `${light} ${muted}${name}`.replace(/\s+/g, " ").trim();
}
