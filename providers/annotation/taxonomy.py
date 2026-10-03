"""Controlled vocabulary for visual-taxonomy-v3.

This module is the one definition of the label families and their values.
The proposal emitter checks every label against it, so a bad label fails
when it is made, and the gallery stores what it emits. To add a value, add
it here.

`unknown` means the value cannot be determined from the evidence. `abstain`
means the assigner deliberately declines. A provider with no signal for a
family abstains -- it never guesses, and it never silently omits the family,
because every proposal carries every family.

v3 (2026-07-26, from the reviewed sample): the enum values are
unchanged, but two fields left the DISPLAY set and two open-vocabulary content
facets joined the record. `weather` and `angle` are still recorded -- evidence
is cheap and the drop is reversible -- and no product surface shows them:
weather abstained on 78% of frames with no precedent facet to justify the
authoring cost, and angle's abstain is the settled-correct answer (both
orientation measures are dead). The content facets (`things`, `actions`) carry
allowlisted tagger vocabulary verbatim; they are multi-value evidence lists,
not single sealed labels, and reach 55%/39% of gold frames at the 0.35 cut.
"""

from __future__ import annotations

TAXONOMY_VERSION = "visual-taxonomy-v3"
PROTOCOL_VERSION = "annotation-protocol-v2"

# Fields a product surface (contact sheet, review UI, eventual gallery) shows.
# Everything else in the record is calibration/reversibility evidence.
DISPLAY_FIELDS = ("setting", "time", "lighting", "color_bias", "saturation",
                  "shot_scale", "composition", "people")
NON_DISPLAY_FIELDS = ("weather", "angle")

# Open-vocabulary multi-value facets. Values are allowlisted WD tag names
# verbatim; the allowlist is the closed set, not this module.
CONTENT_FACETS = ("things", "actions")

SETTING = ("interior", "exterior", "mixed", "abstract", "unknown", "abstain")
TIME = ("day", "night", "twilight", "indeterminate", "unknown", "abstain")
WEATHER = ("clear", "rain", "snow", "fog", "storm", "other", "none-visible",
           "unknown", "abstain")
LIGHTING = ("high-key", "low-key", "backlit", "silhouette", "flat", "mixed",
            "unknown", "abstain")
COLOR_BIAS = ("warm", "cool", "neutral", "mixed", "monochrome", "unknown", "abstain")
SATURATION = ("muted", "balanced", "vivid", "unknown", "abstain")
PALETTE_EVIDENCE = ("available", "abstain")
SHOT_SCALE = ("extreme-wide", "wide", "medium", "close-up", "extreme-close-up",
              "mixed", "unknown", "abstain")
ANGLE = ("eye-level", "high", "low", "dutch", "overhead", "other", "unknown", "abstain")
COMPOSITION = ("centered", "rule-of-thirds", "symmetrical", "asymmetrical",
               "layered", "other", "unknown", "abstain")
PEOPLE = ("zero", "one", "two", "small-group", "crowd", "unclear", "unknown", "abstain")
QUALITY = ("usable", "text-only-card", "prominent-text-overlay",
           "transition-interstitial", "visually-redundant-composition",
           "technical-defect", "unknown", "abstain")
DISPOSITION = ("accept", "reject", "review")

FAMILIES = (
    "setting-time-weather",
    "lighting-color-character",
    "shot-scale",
    "angle-composition",
    "people",
    "quality",
)

VALUES = {
    "setting": SETTING, "time": TIME, "weather": WEATHER,
    "lighting": LIGHTING, "color_bias": COLOR_BIAS, "saturation": SATURATION,
    "palette_evidence": PALETTE_EVIDENCE, "shot_scale": SHOT_SCALE,
    "angle": ANGLE, "composition": COMPOSITION, "people": PEOPLE,
}


def check(field: str, value: str) -> str:
    allowed = VALUES[field]
    if value not in allowed:
        raise ValueError(f"{value!r} is not a {field} value in {TAXONOMY_VERSION}")
    return value
