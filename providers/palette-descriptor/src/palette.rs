//! Palette extraction and colour descriptor payload.
//!
//! Ported from image2lightroom palette.rs (MIT, 2026-07-19).
//! Lightroom preset-generation code intentionally excluded.

use serde::Serialize;

use crate::color::{cbcr, luma, rgb_hue_deg, rgb_sat, LumaStandard};

pub const MIN_CLUSTER_WEIGHT_FRACTION: f32 = 0.006;
pub const RGB_MERGE_DISTANCE: f32 = 42.0;
// An accent: a saturated colour present but spread thin (an anti-aliased
// glow, a gradient), so no single bin clears the floor, that is unlike
// every colour picked by area. At most this many join the representative
// palette, on top of its max_colors.
pub const MAX_ACCENTS: usize = 2;
pub const ACCENT_MIN_SAT: f32 = 0.35;
pub const ACCENT_MIN_DISTANCE: f32 = 2.0;
pub const BAND_HUES: [f32; 8] = [0.0, 30.0, 60.0, 120.0, 180.0, 220.0, 275.0, 320.0];

#[derive(Debug, Clone)]
pub struct PaletteColor {
    pub rgb: [u8; 3],
    pub hex: String,
    pub weight: f32,
    pub hue: f32,
    pub sat: f32,
    pub luma: f32,
    // Joined the representative palette as an accent (see MAX_ACCENTS):
    // shown as a swatch, left out of the by-area statistics.
    pub accent: bool,
}

pub struct ExtractedPalettes {
    pub dominant: Vec<PaletteColor>,
    pub representative: Vec<PaletteColor>,
    pub shadows: Vec<PaletteColor>,
    pub midtones: Vec<PaletteColor>,
    pub highlights: Vec<PaletteColor>,
}

#[derive(Clone, Copy, Default)]
struct Bin {
    weight: f32,
    r: f32,
    g: f32,
    b: f32,
}

#[derive(Clone, Copy)]
struct Candidate {
    weight: f32,
    rgb: [f32; 3],
    accent: bool,
}

#[derive(Clone, Copy)]
enum ToneBucket {
    All,
    Shadows,
    Midtones,
    Highlights,
}

impl ToneBucket {
    fn contains(self, y: f32) -> bool {
        match self {
            ToneBucket::All => true,
            ToneBucket::Shadows => y < 0.33,
            ToneBucket::Midtones => (0.25..0.72).contains(&y),
            ToneBucket::Highlights => y >= 0.67,
        }
    }
}

pub fn extract_palettes(samples: &[u8], max_colors: usize) -> ExtractedPalettes {
    let max_colors = max_colors.max(1);
    let dominant = extract_palette(samples, max_colors, ToneBucket::All, false);
    let representative = extract_palette(samples, max_colors, ToneBucket::All, true);
    let bucket_colors = (max_colors / 2).clamp(3, max_colors);
    let shadows = extract_palette(samples, bucket_colors, ToneBucket::Shadows, true);
    let midtones = extract_palette(samples, bucket_colors, ToneBucket::Midtones, true);
    let highlights = extract_palette(samples, bucket_colors, ToneBucket::Highlights, true);
    ExtractedPalettes {
        dominant,
        representative,
        shadows,
        midtones,
        highlights,
    }
}

fn extract_palette(
    samples: &[u8],
    max_colors: usize,
    bucket: ToneBucket,
    representative: bool,
) -> Vec<PaletteColor> {
    let mut bins = vec![Bin::default(); 16 * 16 * 16];

    for px in samples.chunks_exact(3) {
        let mut r8 = px[0];
        let mut g8 = px[1];
        let mut b8 = px[2];
        let r = r8 as f32 / 255.0;
        let g = g8 as f32 / 255.0;
        let b = b8 as f32 / 255.0;
        let max = r.max(g).max(b);
        let min = r.min(g).min(b);

        if max < 0.085 {
            [r8, g8, b8] = [0, 0, 0];
        } else if min > 0.985 {
            [r8, g8, b8] = [255, 255, 255];
        }

        let r = r8 as f32 / 255.0;
        let g = g8 as f32 / 255.0;
        let b = b8 as f32 / 255.0;
        let sat = rgb_sat(r, g, b);

        let y = luma(r, g, b, LumaStandard::Bt709);
        if !bucket.contains(y) {
            continue;
        }
        let weight = (0.15 + sat * 0.85) * (0.35 + (1.0 - (y - 0.5).abs() * 1.4).max(0.0));
        let idx = ((r8 as usize >> 4) << 8) | ((g8 as usize >> 4) << 4) | (b8 as usize >> 4);
        let bin = &mut bins[idx];
        bin.weight += weight;
        bin.r += r8 as f32 * weight;
        bin.g += g8 as f32 * weight;
        bin.b += b8 as f32 * weight;
    }

    let total_weight: f32 = bins.iter().map(|b| b.weight).sum();
    if total_weight <= 0.0 {
        return Vec::new();
    }

    let to_candidate = |b: &Bin| {
        let inv = 1.0 / b.weight.max(1e-6);
        Candidate {
            weight: b.weight,
            rgb: [b.r * inv, b.g * inv, b.b * inv],
            accent: false,
        }
    };
    let clears_floor = |w: f32| w / total_weight >= MIN_CLUSTER_WEIGHT_FRACTION;
    let mut candidates: Vec<_> = bins
        .iter()
        .filter(|b| clears_floor(b.weight))
        .map(to_candidate)
        .collect();
    candidates.sort_by(|a, b| b.weight.total_cmp(&a.weight));

    let picked = if representative {
        // The floor drops bins one by one, which loses a colour that is
        // present but spread thin: one hue's anti-aliased glow lands in
        // dozens of neighbouring bins, none clearing the floor alone, while
        // a flat field stacks into a few that do (the one-accent-colour
        // frame, 2026-09-30). The sub-floor bins are grouped by RGB
        // nearness; a group whose mass clears the floor, with no surviving
        // bin near it, is offered to the pick as an accent. The picks by
        // area are exactly what they were.
        let mut small: Vec<_> = bins
            .iter()
            .filter(|b| b.weight > 0.0 && !clears_floor(b.weight))
            .copied()
            .collect();
        small.sort_by(|a, b| b.weight.total_cmp(&a.weight));
        let mut groups: Vec<Bin> = Vec::new();
        for b in small {
            let c = to_candidate(&b);
            match groups
                .iter_mut()
                .find(|g| rgb_distance(to_candidate(g).rgb, c.rgb) < RGB_MERGE_DISTANCE)
            {
                Some(g) => {
                    g.weight += b.weight;
                    g.r += b.r;
                    g.g += b.g;
                    g.b += b.b;
                }
                None => groups.push(b),
            }
        }
        let accents: Vec<_> = groups
            .iter()
            .filter(|g| clears_floor(g.weight))
            .map(to_candidate)
            .filter(|c| {
                !candidates
                    .iter()
                    .any(|s| rgb_distance(s.rgb, c.rgb) < RGB_MERGE_DISTANCE)
            })
            .collect();
        pick_representative(candidates, accents, max_colors)
    } else {
        pick_dominant(candidates, max_colors)
    };

    palette_from_picks(picked, total_weight)
}

fn pick_dominant(candidates: Vec<Candidate>, max_colors: usize) -> Vec<Candidate> {
    let mut picked = Vec::new();
    for cand in candidates {
        if picked.len() >= max_colors {
            break;
        }
        let too_close = !is_extreme_anchor(cand.rgb)
            && picked.iter().any(|p: &Candidate| {
                !is_extreme_anchor(p.rgb) && rgb_distance(p.rgb, cand.rgb) < RGB_MERGE_DISTANCE
            });
        if !too_close {
            picked.push(cand);
        }
    }
    picked
}

fn pick_representative(
    mut candidates: Vec<Candidate>,
    accents: Vec<Candidate>,
    max_colors: usize,
) -> Vec<Candidate> {
    let mut anchors = Vec::new();
    candidates.retain(|c| {
        if is_extreme_anchor(c.rgb) && anchors.len() < 2 {
            anchors.push(*c);
            false
        } else {
            true
        }
    });

    candidates.sort_by(|a, b| representative_score(*b).total_cmp(&representative_score(*a)));

    let mut picked = anchors;
    for cand in candidates {
        if picked.len() >= max_colors {
            break;
        }
        if !chroma_eligible_rgb(cand.rgb) {
            continue;
        }
        let too_similar = picked
            .iter()
            .any(|p| representative_distance(p.rgb, cand.rgb) < 1.0);
        if !too_similar {
            picked.push(cand);
        }
    }

    // Accents: saturated, unlike every pick so far, the strongest first.
    // They ride on top of max_colors, so the picks by area, and every
    // statistic taken from them, are exactly what they were without them.
    let mut accents: Vec<_> = accents
        .into_iter()
        .filter(|c| {
            let rgb = rgb01(c.rgb);
            chroma_eligible_rgb(c.rgb) && rgb_sat(rgb[0], rgb[1], rgb[2]) >= ACCENT_MIN_SAT
        })
        .collect();
    accents.sort_by(|a, b| representative_score(*b).total_cmp(&representative_score(*a)));
    let mut taken = 0;
    for acc in accents {
        if taken >= MAX_ACCENTS {
            break;
        }
        let unlike = picked
            .iter()
            .all(|p| representative_distance(p.rgb, acc.rgb) >= ACCENT_MIN_DISTANCE);
        if !unlike {
            continue;
        }
        picked.push(Candidate { accent: true, ..acc });
        taken += 1;
    }

    // By area first, then the accents, each by weight.
    picked.sort_by(|a, b| a.accent.cmp(&b.accent).then(b.weight.total_cmp(&a.weight)));
    picked
}

// Weights: a pick by area carries its share of the picked-by-area mass, as
// before. An accent pools a whole gradient where a pick by area is one bin
// that lost its gradient to the floor, so the two are not on one scale:
// an accent carries its share of the frame's whole weight instead, which
// is the plainer number, and the by-area weights still sum to one.
fn palette_from_picks(picked: Vec<Candidate>, total_weight: f32) -> Vec<PaletteColor> {
    let picked_total: f32 = picked.iter().filter(|c| !c.accent).map(|c| c.weight).sum::<f32>().max(1e-6);
    picked
        .into_iter()
        .map(|c| {
            let rgb8 = [
                c.rgb[0].round().clamp(0.0, 255.0) as u8,
                c.rgb[1].round().clamp(0.0, 255.0) as u8,
                c.rgb[2].round().clamp(0.0, 255.0) as u8,
            ];
            let share = if c.accent { c.weight / total_weight.max(1e-6) } else { c.weight / picked_total };
            PaletteColor {
                accent: c.accent,
                ..palette_color_from_rgb(rgb8, share)
            }
        })
        .collect()
}

pub fn palette_color_from_rgb(rgb: [u8; 3], weight: f32) -> PaletteColor {
    let (r, g, b) = (
        rgb[0] as f32 / 255.0,
        rgb[1] as f32 / 255.0,
        rgb[2] as f32 / 255.0,
    );
    PaletteColor {
        rgb,
        hex: format!("#{:02X}{:02X}{:02X}", rgb[0], rgb[1], rgb[2]),
        weight,
        hue: rgb_hue_deg(r, g, b).unwrap_or(0.0),
        sat: rgb_sat(r, g, b),
        luma: luma(r, g, b, LumaStandard::Bt709),
        accent: false,
    }
}

fn representative_score(c: Candidate) -> f32 {
    let rgb = rgb01(c.rgb);
    let sat = rgb_sat(rgb[0], rgb[1], rgb[2]);
    let y = luma(rgb[0], rgb[1], rgb[2], LumaStandard::Bt709);
    let luma_balance = (1.0 - (y - 0.45).abs() * 1.6).clamp(0.25, 1.0);
    c.weight.powf(0.62) * (0.35 + sat) * luma_balance
}

fn representative_distance(a: [f32; 3], b: [f32; 3]) -> f32 {
    let ar = rgb01(a);
    let br = rgb01(b);
    let ah = rgb_hue_deg(ar[0], ar[1], ar[2]).unwrap_or(0.0);
    let bh = rgb_hue_deg(br[0], br[1], br[2]).unwrap_or(0.0);
    let hue_dist = circular_distance(ah, bh) / 55.0;
    let al = luma(ar[0], ar[1], ar[2], LumaStandard::Bt709);
    let bl = luma(br[0], br[1], br[2], LumaStandard::Bt709);
    let luma_dist = (al - bl).abs() / 0.20;
    let rgb_dist = rgb_distance(a, b) / 70.0;
    hue_dist.min(2.0) + luma_dist + rgb_dist.min(1.5)
}

pub fn hue_supports(colors: &[PaletteColor]) -> [f32; 8] {
    std::array::from_fn(|i| {
        colors
            .iter()
            .filter(|c| chroma_eligible(c))
            .map(|c| {
                let distance = circular_distance(BAND_HUES[i], c.hue);
                let proximity = (1.0 - distance / 75.0).clamp(0.0, 1.0);
                proximity * c.weight * (0.25 + c.sat)
            })
            .sum::<f32>()
    })
}

pub fn normalized_supports(mut supports: [f32; 8]) -> [f32; 8] {
    let total = supports.iter().sum::<f32>();
    if total > 1e-6 {
        for support in &mut supports {
            *support /= total;
        }
    }
    supports
}

pub fn mean_chroma(colors: &[PaletteColor]) -> f32 {
    let mut total = 0.0;
    let mut weight = 0.0;
    for color in colors.iter().filter(|c| chroma_eligible(c)) {
        let w = color.weight * (0.25 + color.sat);
        total += color.sat * w;
        weight += w;
    }
    if weight > 1e-6 {
        total / weight
    } else {
        0.0
    }
}

pub fn out_of_palette_mass(reference: &[PaletteColor], target: &[PaletteColor]) -> f32 {
    let ref_colors: Vec<_> = reference.iter().filter(|c| chroma_eligible(c)).collect();
    if ref_colors.is_empty() {
        return 0.0;
    }
    let mut out = 0.0;
    let mut total = 0.0;
    for color in target.iter().filter(|c| chroma_eligible(c)) {
        let weight = color.weight * (0.25 + color.sat);
        let proximity = ref_colors
            .iter()
            .map(|r| (1.0 - circular_distance(r.hue, color.hue) / 70.0).clamp(0.0, 1.0))
            .fold(0.0, f32::max);
        out += (1.0 - proximity) * weight;
        total += weight;
    }
    if total > 1e-6 {
        out / total
    } else {
        0.0
    }
}

pub fn chroma_eligible(c: &PaletteColor) -> bool {
    c.sat >= 0.08 && c.luma >= 0.10 && c.luma <= 0.94
}

fn chroma_eligible_rgb(rgb: [f32; 3]) -> bool {
    let rgb = rgb01(rgb);
    let sat = rgb_sat(rgb[0], rgb[1], rgb[2]);
    let y = luma(rgb[0], rgb[1], rgb[2], LumaStandard::Bt709);
    sat >= 0.08 && (0.10..=0.94).contains(&y)
}

pub fn is_extreme_anchor(rgb: [f32; 3]) -> bool {
    let max = rgb[0].max(rgb[1]).max(rgb[2]);
    let min = rgb[0].min(rgb[1]).min(rgb[2]);
    max < 8.0 || min > 247.0
}

fn rgb_distance(a: [f32; 3], b: [f32; 3]) -> f32 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()
}

fn rgb01(rgb: [f32; 3]) -> [f32; 3] {
    [rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0]
}

pub fn circular_distance(a: f32, b: f32) -> f32 {
    let d = (a - b).abs().rem_euclid(360.0);
    d.min(360.0 - d)
}

/// Palette distance between two representative palettes.
/// Reshaped from source_profile's three distance components.
pub fn palette_distance(a: &[PaletteColor], b: &[PaletteColor]) -> PaletteDistance {
    let ref_supports = normalized_supports(hue_supports(a));
    let target_supports = normalized_supports(hue_supports(b));
    let hue_family_l1 = ref_supports
        .iter()
        .zip(target_supports.iter())
        .map(|(x, y)| (x - y).abs())
        .sum::<f32>()
        * 0.5;
    let out_of_palette = out_of_palette_mass(a, b);
    let chroma_delta = mean_chroma(b) - mean_chroma(a);
    PaletteDistance {
        hue_family_l1,
        out_of_palette_mass: out_of_palette,
        source_chroma_delta: chroma_delta,
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct PaletteDistance {
    pub hue_family_l1: f32,
    pub out_of_palette_mass: f32,
    pub source_chroma_delta: f32,
}

/// Compute luma statistics over sampled pixels.
pub fn luma_stats(samples: &[u8]) -> LumaStats {
    let mut luma_values: Vec<f32> = samples
        .chunks_exact(3)
        .map(|px| {
            let r = px[0] as f32 / 255.0;
            let g = px[1] as f32 / 255.0;
            let b = px[2] as f32 / 255.0;
            luma(r, g, b, LumaStandard::Bt709)
        })
        .collect();
    luma_values.sort_by(|a, b| a.total_cmp(b));
    let n = luma_values.len();
    if n == 0 {
        return LumaStats {
            mean: 0.0,
            p05: 0.0,
            p50: 0.0,
            p95: 0.0,
        };
    }
    let mean = luma_values.iter().sum::<f32>() / n as f32;
    let p05 = percentile_nearest_rank(&luma_values, 0.05);
    let p50 = percentile_nearest_rank(&luma_values, 0.50);
    let p95 = percentile_nearest_rank(&luma_values, 0.95);
    LumaStats { mean, p05, p50, p95 }
}

fn percentile_nearest_rank(sorted: &[f32], p: f32) -> f32 {
    if sorted.is_empty() {
        return 0.0;
    }
    let idx = ((p * sorted.len() as f32).round() as usize).min(sorted.len() - 1);
    sorted[idx]
}

#[derive(Debug, Clone, Serialize)]
pub struct LumaStats {
    pub mean: f32,
    pub p05: f32,
    pub p50: f32,
    pub p95: f32,
}

/// Compute temperature (Cb, Cr means) over chroma-eligible representative colours.
pub fn temperature(representative: &[PaletteColor]) -> Temperature {
    let mut cb_sum = 0.0;
    let mut cr_sum = 0.0;
    let mut w_sum = 0.0;
    for c in representative.iter().filter(|c| chroma_eligible(c)) {
        let r = c.rgb[0] as f32 / 255.0;
        let g = c.rgb[1] as f32 / 255.0;
        let b = c.rgb[2] as f32 / 255.0;
        let (cb, cr) = cbcr(r, g, b, LumaStandard::Bt709);
        let w = c.weight * (0.25 + c.sat);
        cb_sum += cb * w;
        cr_sum += cr * w;
        w_sum += w;
    }
    if w_sum <= 0.0 {
        return Temperature {
            cb_mean: 0.0,
            cr_mean: 0.0,
        };
    }
    Temperature {
        cb_mean: cb_sum / w_sum,
        cr_mean: cr_sum / w_sum,
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct Temperature {
    pub cb_mean: f32,
    pub cr_mean: f32,
}

/// Check if anchors were picked in the representative palette.
pub fn anchors(representative: &[PaletteColor]) -> Anchors {
    let mut black = false;
    let mut white = false;
    for c in representative {
        let max = c.rgb[0].max(c.rgb[1]).max(c.rgb[2]);
        let min = c.rgb[0].min(c.rgb[1]).min(c.rgb[2]);
        if (max as f32) < 8.0 {
            black = true;
        }
        if (min as f32) > 247.0 {
            white = true;
        }
    }
    Anchors { black, white }
}

#[derive(Debug, Clone, Serialize)]
pub struct Anchors {
    pub black: bool,
    pub white: bool,
}

/// The v1 descriptor payload.
#[derive(Debug, Clone, Serialize)]
pub struct DescriptorPayload {
    pub descriptor_schema: String,
    pub provider: String,
    pub provider_version: String,
    pub palette: PaletteBlock,
    pub hue_family: HueFamily,
    pub luma: LumaStats,
    pub chroma: ChromaBlock,
    pub temperature: Temperature,
    pub anchors: Anchors,
    pub sampling: SamplingBlock,
    pub assumed_input: AssumedInput,
    pub representation: Representation,
}

#[derive(Debug, Clone, Serialize)]
pub struct PaletteBlock {
    pub dominant: Vec<PaletteEntry>,
    pub representative: Vec<PaletteEntry>,
    pub shadows: Vec<PaletteEntry>,
    pub midtones: Vec<PaletteEntry>,
    pub highlights: Vec<PaletteEntry>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PaletteEntry {
    pub hex: String,
    pub rgb: [u8; 3],
    pub weight: f32,
    pub hue_deg: f32,
    pub sat: f32,
    pub luma: f32,
    pub accent: bool,
}

impl From<&PaletteColor> for PaletteEntry {
    fn from(c: &PaletteColor) -> Self {
        PaletteEntry {
            hex: c.hex.clone(),
            rgb: c.rgb,
            weight: c.weight,
            hue_deg: c.hue,
            sat: c.sat,
            luma: c.luma,
            accent: c.accent,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct HueFamily {
    pub bands: [f32; 8],
    pub band_centers_deg: [f32; 8],
}

#[derive(Debug, Clone, Serialize)]
pub struct ChromaBlock {
    pub mean_sat_weighted: f32,
}

#[derive(Debug, Clone, Serialize)]
pub struct SamplingBlock {
    pub max_samples: usize,
    pub stride: usize,
    pub sampled_pixels: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct AssumedInput {
    pub pixel_format: String,
    pub color_intent: String,
    pub icc: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Representation {
    pub hue: String,
    pub luma: String,
    pub sat: String,
}

/// Build the full descriptor payload from extracted palettes and sampling info.
pub fn build_descriptor(
    extracted: &ExtractedPalettes,
    samples: &[u8],
    max_samples: usize,
    stride: usize,
    sampled_pixels: usize,
) -> DescriptorPayload {
    let luma_stats = luma_stats(samples);
    // The by-area statistics (what the frame IS: its hue mass, chroma,
    // temperature) come from the picks by area; an accent is shown, not
    // counted, so a cool frame with one warm accent stays cool.
    let by_area: Vec<PaletteColor> = extracted
        .representative
        .iter()
        .filter(|c| !c.accent)
        .cloned()
        .collect();
    let temp = temperature(&by_area);
    let anch = anchors(&by_area);
    let hue_family_bands = normalized_supports(hue_supports(&by_area));
    let mean_chroma_val = mean_chroma(&by_area);

    DescriptorPayload {
        descriptor_schema: "hikari-color-descriptor/1".to_string(),
        provider: "palette-descriptor".to_string(),
        provider_version: "0.2.0".to_string(),
        palette: PaletteBlock {
            dominant: extracted.dominant.iter().map(PaletteEntry::from).collect(),
            representative: extracted.representative.iter().map(PaletteEntry::from).collect(),
            shadows: extracted.shadows.iter().map(PaletteEntry::from).collect(),
            midtones: extracted.midtones.iter().map(PaletteEntry::from).collect(),
            highlights: extracted.highlights.iter().map(PaletteEntry::from).collect(),
        },
        hue_family: HueFamily {
            bands: hue_family_bands,
            band_centers_deg: BAND_HUES,
        },
        luma: luma_stats,
        chroma: ChromaBlock {
            mean_sat_weighted: mean_chroma_val,
        },
        temperature: temp,
        anchors: anch,
        sampling: SamplingBlock {
            max_samples,
            stride,
            sampled_pixels,
        },
        assumed_input: AssumedInput {
            pixel_format: "rgb24".to_string(),
            color_intent: "sRGB SDR full-range".to_string(),
            icc: "none (displayed code values)".to_string(),
        },
        representation: Representation {
            hue: "hsv".to_string(),
            luma: "bt709-gamma".to_string(),
            sat: "hsv".to_string(),
        },
    }
}
