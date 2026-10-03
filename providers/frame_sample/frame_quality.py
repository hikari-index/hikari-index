"""Frame-level quality and framing-distinctness measures for publishing.

Both measures read the extracted PNG and are cheap and deterministic, so they
belong on the source worker beside the rest of frame preparation.

They exist because `scene_score` describes the cut that *opened* a shot, not the
frame sampled inside it. Ranking on scene_score alone promoted fades: on a real
run a pure black frame scored 124.4 and topped the published sheet. `measure`
supplies the missing frame-level signal, used both as a reject floor and as the
within-shot representative score.

`signature`/`framing_distance` answer a separate question -- are two frames from
one shot the same picture? Perceptual hashing cannot. Measured across the 32
frames of the 2026-07-23 passage runs, same-shot twins reached a pHash distance
of 34 while unrelated frames sat at a median of 31: low-contrast cel art keeps
the DCT coefficients near the median, so micro-motion flips bits wholesale. A
downscaled pixel comparison asks about framing directly, and on those same
frames it separated cleanly -- twins spanned 0.060-0.149 and every genuinely
different pair started at 0.198.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

# Grids are fixed rather than configurable: the thresholds below were measured
# against these sizes and are meaningless at another scale.
QUALITY_GRID = (256, 144)
SIGNATURE_GRID = (32, 18)
ENTROPY_BINS = 64

# Floor defaults. Real dark-but-legitimate frames measured detail 0.0068 (a lit
# doorway) and 0.0086 (a shadowed door); the junk measured 0.0000 (pure black)
# and 0.0018/0.0041 (near-black). 0.005 splits them with margin either side.
DEFAULT_MIN_DETAIL = 0.005
DEFAULT_MIN_ENTROPY = 2.5

# The floor only fires on frames whose luma sits at an extreme, so it cannot
# reject a well-exposed but deliberately soft image. research/01 warns against a
# single sharpness cutoff because anime uses flat fields, diffusion, soft focus,
# monochrome, silhouettes, and line art on purpose. Every rejection measured on
# real material was a blank: mean luma 0.008 and 0.049 (black) or 0.951, 0.951
# and 0.981 (white flash). The nearest survivors sat at 0.131 and 0.844, so
# these bounds keep both sides clear with margin and change no observed verdict.
DEFAULT_DARK_LUMA = 0.10
DEFAULT_BRIGHT_LUMA = 0.90

# Failing the floor is not one verdict, and a review of real runs proved it.
# 242 rejects were read by eye across nineteen works: 43% were frames that
# belonged in the library, and the error rate ran from 0% on one series to
# 72% on another. Neither detail nor entropy separates a title card from a
# composition -- across 146 labelled rejects keeper detail spans 0.0005-0.0204
# and card detail 0.0011-0.0188, full overlap. So the floor cannot be re-tuned
# into precision, and a card detector is not available at this layer.
#
# `luma_stddev` can do the one job that has to be exact. A fade or flash is one
# value across the whole frame; a silhouette or a card is not. The labelled
# blanks are sharply bimodal: 22 of the 25 sit at or below 0.0013 with 17 at
# exactly zero, then nothing at all until 0.0156, and the nearest labelled
# keeper is at 0.0149. Every cut in that empty band scores identically -- 22
# blanks, zero cards, zero keepers -- so the number is chosen for margin, not
# for score. 0.005 sits mid-band, 3.8x above the highest blank and 3.0x below
# the nearest keeper; 0.010 scores the same but leaves only 1.5x on the side
# where being wrong deletes a composition. The three blanks above the band
# survive into review instead, which is the direction that costs nothing.
#
# Numerically equal to DEFAULT_MIN_DETAIL by coincidence. Different measure,
# different evidence, no shared meaning.
DEFAULT_BLANK_LUMA_STDDEV = 0.005

# The cut is the only destructive number in this module, so it is bounded by
# the evidence rather than left open. The lowest luma_stddev measured on a
# labelled keeper is a two-figure silhouette against a white sky:
LOWEST_LABELLED_KEEPER_STDDEV = 0.014886
# The bound must round DOWN from that, not to it. `floor_disposition` deletes on
# `<=`, and the CLI admits a value `<=` this bound, so a bound of 0.0149 made
# 0.014886 <= 0.0149 true and the documented maximum deleted the very keeper
# that justified it. Found by an adversarial audit, not by a test.
MAX_BLANK_LUMA_STDDEV = 0.0148

# Framing distances, from the measured gap described above.
DEFAULT_INTRA_SHOT_DISTINCT = 0.17
DEFAULT_CROSS_SHOT_DUPLICATE = 0.10

# Detail is quantized before ranking so that frames of effectively equal detail
# fall through to the midpoint tiebreak instead of splitting on noise.
DETAIL_RANK_PRECISION = 3


@dataclass(frozen=True)
class FrameQuality:
    luma_mean: float
    luma_stddev: float
    detail: float
    entropy: float

    def to_dict(self) -> dict:
        return {
            "luma_mean": round(self.luma_mean, 6),
            "luma_stddev": round(self.luma_stddev, 6),
            "detail": round(self.detail, 6),
            "entropy": round(self.entropy, 6),
        }


@dataclass(frozen=True)
class FrameAnalysis:
    quality: FrameQuality
    signature: Any  # numpy array; kept out of manifests, comparison only

    @property
    def rank_key(self) -> float:
        """Detail carries the representative score; see DETAIL_RANK_PRECISION."""
        return round(self.quality.detail, DETAIL_RANK_PRECISION)


def analyze(png_bytes: bytes) -> FrameAnalysis:
    """Decode once and derive both the quality metrics and the framing signature."""
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(png_bytes))
    image.load()

    luma = np.asarray(
        image.convert("L").resize(QUALITY_GRID, Image.BILINEAR), dtype=np.float64
    ) / 255.0
    # Mean absolute gradient stands in for edge energy: it is the cheapest
    # measure that separates a drawn frame from a flat fade.
    detail = float(
        (
            np.abs(np.diff(luma, axis=1)).mean()
            + np.abs(np.diff(luma, axis=0)).mean()
        )
        / 2.0
    )
    counts, _ = np.histogram(luma, bins=ENTROPY_BINS, range=(0.0, 1.0))
    probabilities = counts[counts > 0] / counts.sum()
    # A single occupied bin yields -0.0; clamp so audit records read cleanly.
    entropy = max(0.0, float(-(probabilities * np.log2(probabilities)).sum()))

    signature = np.asarray(
        image.convert("RGB").resize(SIGNATURE_GRID, Image.BOX), dtype=np.float64
    ) / 255.0

    return FrameAnalysis(
        quality=FrameQuality(
            luma_mean=float(luma.mean()),
            luma_stddev=float(luma.std()),
            detail=detail,
            entropy=entropy,
        ),
        signature=signature,
    )


def framing_distance(first: Any, second: Any) -> float:
    """Root-mean-square distance between two signatures, in 0..1."""
    import numpy as np

    return float(np.sqrt(((first - second) ** 2).mean()))


def is_luma_extreme(
    quality: FrameQuality,
    dark_luma: float = DEFAULT_DARK_LUMA,
    bright_luma: float = DEFAULT_BRIGHT_LUMA,
) -> bool:
    """Is this frame nearly all black or nearly all white?"""
    return quality.luma_mean <= dark_luma or quality.luma_mean >= bright_luma


CLEARED = "cleared"
BLANK = "blank"
REVIEW = "review"


def floor_disposition(
    quality: FrameQuality,
    min_detail: float = DEFAULT_MIN_DETAIL,
    min_entropy: float = DEFAULT_MIN_ENTROPY,
    dark_luma: float = DEFAULT_DARK_LUMA,
    bright_luma: float = DEFAULT_BRIGHT_LUMA,
    blank_luma_stddev: float = DEFAULT_BLANK_LUMA_STDDEV,
) -> tuple[str, str]:
    """Sort a frame into `cleared`, `blank`, or `review`, with the reason.

    A frame is questioned only when it is BOTH featureless and luma-extreme.
    Requiring the luma condition is what keeps this a blank detector rather than
    a sharpness cutoff: a well-exposed frame is never questioned here however
    soft it is, so deliberate diffusion, flat fields, and soft focus are
    structurally safe. Within that case both featurelessness tests still
    apply -- detail alone would pass fine dither over an empty field, entropy
    alone would pass a two-tone title card.

    Only `blank` is destructive. It is the one verdict measured precise enough
    to act on unsupervised, and its cost of being wrong is a deleted frame no
    one ever sees. `review` publishes the frame and says so, because on the
    reviewed runs the alternative deleted a real composition 43% of the time and no
    threshold available here tells the two apart.
    """
    if not is_luma_extreme(quality, dark_luma, bright_luma):
        return CLEARED, ""
    if quality.detail >= min_detail and quality.entropy >= min_entropy:
        return CLEARED, ""

    side = "near-black" if quality.luma_mean <= dark_luma else "near-white"
    failures = [f"{side} (luma_mean {quality.luma_mean:.6f})"]
    if quality.detail < min_detail:
        failures.append(f"detail {quality.detail:.6f} < {min_detail}")
    if quality.entropy < min_entropy:
        failures.append(f"entropy {quality.entropy:.6f} < {min_entropy}")
    reason = "; ".join(failures)

    if quality.luma_stddev <= blank_luma_stddev:
        return BLANK, f"{reason}; luma_stddev {quality.luma_stddev:.6f} <= " \
                      f"{blank_luma_stddev}"
    return REVIEW, f"{reason}; kept for review at luma_stddev " \
                   f"{quality.luma_stddev:.6f}"
