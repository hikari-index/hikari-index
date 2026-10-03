//! Image decoding to raw 8-bit sRGB pixels.
//!
//! Ported from image2lightroom scope-core decode.rs (MIT, 2026-07-19).
//! Adapted for PNG-only decoding via the `image` crate.

use std::path::Path;

/// A decoded image: interleaved 8-bit sRGB RGB data.
pub struct DecodedImage {
    pub width: u32,
    pub height: u32,
    pub rgb: Vec<u8>,
}

impl DecodedImage {
    /// Load a PNG from disk. 16-bit sources are reduced to 8-bit, alpha is dropped.
    pub fn load(path: &Path) -> Result<Self, String> {
        let img = image::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
        let rgb = img.to_rgb8();
        Ok(Self {
            width: rgb.width(),
            height: rgb.height(),
            rgb: rgb.into_raw(),
        })
    }

    /// Subsampled view of the pixel data for analysis, keeping at most
    /// `max_pixels` samples via uniform striding.
    /// Returns (samples_vec, stride, sampled_pixel_count).
    pub fn samples(&self, max_pixels: usize) -> (Vec<u8>, usize, usize) {
        let total = (self.rgb.len() / 3).max(1);
        let stride = total.div_ceil(max_pixels).max(1);
        if stride == 1 {
            return (self.rgb.clone(), 1, total);
        }
        let mut out = Vec::with_capacity((total / stride + 1) * 3);
        for i in (0..total).step_by(stride) {
            out.extend_from_slice(&self.rgb[i * 3..i * 3 + 3]);
        }
        let count = out.len() / 3;
        (out, stride, count)
    }
}
