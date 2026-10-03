"""Map a colour descriptor onto the taxonomy families it can actually support.

The palette descriptor already runs on every published candidate and emits
hue-family mass, weighted saturation, and luma percentiles. Nothing turned those
numbers into taxonomy values, so three families sat blank while their evidence
was being computed and discarded.

Thresholds are PROVISIONAL. They were chosen against the 32 published frames of
the 2026-07-23 passage runs, which is two titles -- enough to show the rules
order real material correctly, nowhere near enough to calibrate. The protocol
requires per-label calibration on the reviewed sample before any of this is measured.

Colour bias reads hue-family mass rather than the Cb/Cr means. Cb/Cr deviations
shrink as a scene darkens, so a dark orange interior measured only +0.079 there
and would have been called neutral; on hue mass the same frame reads 1.00 warm.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import taxonomy

# hue_family.band_centers_deg is [0, 30, 60, 120, 180, 220, 275, 320].
# 120 (green) is deliberately in neither group: it reads warm or cool by
# context, and forcing it either way mislabels foliage.
WARM_BANDS = (0, 1, 2, 7)
COOL_BANDS = (4, 5, 6)

# Both sides carrying real mass means a genuinely mixed frame -- warm interior
# against a cool window. Measured examples: a classroom (0.48/0.44) and a green
# door in warm stone (0.57/0.42).
MIXED_MIN_MASS = 0.25
BIAS_WARM = 0.35
BIAS_COOL = -0.35

# Observed weighted saturation over 32 real frames ran 0.158-0.812. True
# greyscale sits far below the bottom of that range; no frame in the sample was
# monochrome, so this bound is asserted, not measured.
MONOCHROME_SAT = 0.12
SATURATION_MUTED = 0.30
SATURATION_VIVID = 0.60

# Luma reads high-key/low-key only. Backlit, silhouette, and flat need spatial
# evidence this descriptor does not carry, so they abstain rather than guess.
LOW_KEY_MEAN = 0.30
LOW_KEY_MAX_P95 = 0.75
HIGH_KEY_MEAN = 0.65
HIGH_KEY_MIN_P05 = 0.30


@dataclass(frozen=True)
class PaletteLabels:
    color_bias: str
    saturation: str
    lighting: str
    palette_evidence: str
    color_bias_score: float
    saturation_score: float
    lighting_score: float


def _bias_scores(descriptor: dict) -> tuple[float, float, float]:
    bands = descriptor["hue_family"]["bands"]
    warm = sum(bands[i] for i in WARM_BANDS)
    cool = sum(bands[i] for i in COOL_BANDS)
    return warm, cool, warm - cool


def color_bias(descriptor: dict) -> tuple[str, float]:
    saturation = descriptor["chroma"]["mean_sat_weighted"]
    if saturation < MONOCHROME_SAT:
        return "monochrome", 0.6
    warm, cool, bias = _bias_scores(descriptor)
    if warm >= MIXED_MIN_MASS and cool >= MIXED_MIN_MASS:
        return "mixed", round(min(warm, cool) / MIXED_MIN_MASS * 0.5, 4)
    if bias >= BIAS_WARM:
        return "warm", round(min(1.0, abs(bias)), 4)
    if bias <= BIAS_COOL:
        return "cool", round(min(1.0, abs(bias)), 4)
    # No frame in the calibration sample landed here; the band is untested.
    return "neutral", round(1.0 - abs(bias) / BIAS_WARM, 4)


def saturation(descriptor: dict) -> tuple[str, float]:
    value = descriptor["chroma"]["mean_sat_weighted"]
    if value < SATURATION_MUTED:
        return "muted", round(min(1.0, (SATURATION_MUTED - value) / SATURATION_MUTED + 0.5), 4)
    if value > SATURATION_VIVID:
        return "vivid", round(min(1.0, (value - SATURATION_VIVID) / (1 - SATURATION_VIVID) + 0.5), 4)
    return "balanced", 0.7


def lighting(descriptor: dict) -> tuple[str, float]:
    luma = descriptor["luma"]
    mean, p05, p95 = luma["mean"], luma["p05"], luma["p95"]
    if mean < LOW_KEY_MEAN and p95 < LOW_KEY_MAX_P95:
        return "low-key", round(min(1.0, (LOW_KEY_MEAN - mean) / LOW_KEY_MEAN + 0.5), 4)
    if mean > HIGH_KEY_MEAN and p05 > HIGH_KEY_MIN_P05:
        return "high-key", round(min(1.0, (mean - HIGH_KEY_MEAN) / (1 - HIGH_KEY_MEAN) + 0.5), 4)
    # Bright with crushed shadows, or dark with strong highlights: real lighting
    # character, but not one this evidence can name.
    return "abstain", 0.0


#: The measurement blocks every rule below reads. A descriptor missing any of
#: them carries no colour evidence, whatever else it says.
REQUIRED_BLOCKS = ("chroma", "luma", "hue_family")


def has_evidence(descriptor: Optional[dict]) -> bool:
    """Whether this descriptor actually carries colour measurements.

    The colour provider emits a short abstain record -- `status`, `reason`, and
    nothing else -- for a frame it cannot describe, such as one with no usable
    palette colours. That record is a well-formed descriptor file, so presence on
    disk proves nothing; only the measurement blocks do.
    """
    if descriptor is None or descriptor.get("status") == "abstained":
        return False
    return all(block in descriptor for block in REQUIRED_BLOCKS)


def from_descriptor(descriptor: Optional[dict]) -> PaletteLabels:
    """Derive every family the colour descriptor supports, or abstain wholesale."""
    if not has_evidence(descriptor):
        return PaletteLabels(
            color_bias="abstain", saturation="abstain", lighting="abstain",
            palette_evidence="abstain",
            color_bias_score=0.0, saturation_score=0.0, lighting_score=0.0,
        )
    bias_label, bias_score = color_bias(descriptor)
    sat_label, sat_score = saturation(descriptor)
    light_label, light_score = lighting(descriptor)
    return PaletteLabels(
        color_bias=taxonomy.check("color_bias", bias_label),
        saturation=taxonomy.check("saturation", sat_label),
        lighting=taxonomy.check("lighting", light_label),
        palette_evidence="available",
        color_bias_score=bias_score,
        saturation_score=sat_score,
        lighting_score=light_score,
    )
