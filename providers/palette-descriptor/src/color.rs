//! sRGB / Rec.709 / Rec.601 color math on code values.
//!
//! Ported from image2lightroom scope-core color.rs (MIT, 2026-07-19).
//! All functions take channel values normalized to 0..=1.

/// Luma / chroma coefficient standard.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum LumaStandard {
    #[default]
    Bt709,
    Bt601,
}

impl LumaStandard {
    pub fn coefficients(self) -> (f32, f32, f32) {
        match self {
            LumaStandard::Bt709 => (0.2126, 0.7152, 0.0722),
            LumaStandard::Bt601 => (0.299, 0.587, 0.114),
        }
    }

    fn chroma_divisors(self) -> (f32, f32) {
        let (kr, _, kb) = self.coefficients();
        (2.0 * (1.0 - kb), 2.0 * (1.0 - kr))
    }
}

/// Luma Y' of gamma-encoded R'G'B'.
pub fn luma(r: f32, g: f32, b: f32, std: LumaStandard) -> f32 {
    let (kr, kg, kb) = std.coefficients();
    kr * r + kg * g + kb * b
}

/// Normalized (Cb, Cr) chroma of R'G'B', each in -0.5..=0.5.
pub fn cbcr(r: f32, g: f32, b: f32, std: LumaStandard) -> (f32, f32) {
    let y = luma(r, g, b, std);
    let (db, dr) = std.chroma_divisors();
    ((b - y) / db, (r - y) / dr)
}

/// HSV-style hue of R'G'B' in degrees 0..360 (red = 0).
/// Returns None for neutral pixels where hue is undefined.
pub fn rgb_hue_deg(r: f32, g: f32, b: f32) -> Option<f32> {
    let max = r.max(g).max(b);
    let min = r.min(g).min(b);
    let c = max - min;
    if c <= f32::EPSILON {
        return None;
    }
    let h = if max == r {
        (g - b) / c
    } else if max == g {
        2.0 + (b - r) / c
    } else {
        4.0 + (r - g) / c
    };
    Some((h * 60.0).rem_euclid(360.0))
}

/// HSV saturation (chroma / max), 0..=1. Zero for black.
pub fn rgb_sat(r: f32, g: f32, b: f32) -> f32 {
    let max = r.max(g).max(b);
    let min = r.min(g).min(b);
    if max <= f32::EPSILON {
        0.0
    } else {
        (max - min) / max
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn luma_of_white_is_one() {
        for std in [LumaStandard::Bt709, LumaStandard::Bt601] {
            assert!((luma(1.0, 1.0, 1.0, std) - 1.0).abs() < 1e-5);
        }
    }

    #[test]
    fn chroma_of_gray_is_zero() {
        for std in [LumaStandard::Bt709, LumaStandard::Bt601] {
            let (cb, cr) = cbcr(0.5, 0.5, 0.5, std);
            assert!(cb.abs() < 1e-6 && cr.abs() < 1e-6);
        }
    }

    #[test]
    fn chroma_extremes_are_half() {
        for std in [LumaStandard::Bt709, LumaStandard::Bt601] {
            let (cb, _) = cbcr(0.0, 0.0, 1.0, std);
            let (_, cr) = cbcr(1.0, 0.0, 0.0, std);
            assert!((cb - 0.5).abs() < 1e-5, "{std:?} Cb of blue = {cb}");
            assert!((cr - 0.5).abs() < 1e-5, "{std:?} Cr of red = {cr}");
        }
    }

    #[test]
    fn hsv_hue_primaries() {
        assert_eq!(rgb_hue_deg(1.0, 0.0, 0.0), Some(0.0));
        assert_eq!(rgb_hue_deg(0.0, 1.0, 0.0), Some(120.0));
        assert_eq!(rgb_hue_deg(0.0, 0.0, 1.0), Some(240.0));
        assert_eq!(rgb_hue_deg(0.5, 0.5, 0.5), None);
    }

    #[test]
    fn rgb_sat_pure_colors() {
        assert!((rgb_sat(1.0, 0.0, 0.0) - 1.0).abs() < 1e-6);
        assert!((rgb_sat(0.0, 1.0, 0.0) - 1.0).abs() < 1e-6);
        assert!((rgb_sat(0.0, 0.0, 1.0) - 1.0).abs() < 1e-6);
        assert!(rgb_sat(0.5, 0.5, 0.5).abs() < 1e-6);
        assert!(rgb_sat(0.0, 0.0, 0.0).abs() < 1e-6);
    }

    #[test]
    fn rgb_sat_mixed() {
        let s = rgb_sat(0.8, 0.2, 0.1);
        assert!((s - 0.875).abs() < 1e-5);
    }
}
