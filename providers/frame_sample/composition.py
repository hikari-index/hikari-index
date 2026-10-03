"""Spatial composition geometry, measured on the host-local worker.

`research/02` prescribes composition as spatial rules PLUS controlled zero-shot.
The zero-shot half was built and is dead -- 0.532 against a 0.554 majority
baseline over 3,190 frames. This is the other half, and it needs no model, so it
belongs here beside `frame_quality` rather than on the inference worker.

Only the measurement lives here. The *verdict* needs the tagger (to tell a
symmetric title card from a symmetric composition) and the face boxes (for
subject position), both of which come back from the inference worker, so it is
assembled in `providers/annotation`. Splitting it that way keeps learned models
off this worker, per the architecture rule.

What is deliberately NOT here:

- **Subject position without a face.** Five estimators were measured against
  face-box position as ground truth; the best reached Pearson 0.689 behind a
  circular gate, gained +2.5pp over the majority baseline on the hardest title,
  and its `centered` class was dominated by title cards. The fifth,
  area-normalised region competition, was the last live lead and is also dead:
  pooled Pearson 0.477 over 272 single-face frames against a 0.689 bar, MAE
  0.112 against a 0.06 tolerance, losing to the plain gradient centroid it was
  meant to replace. Faceless frames abstain and there is no remaining lead.
  See FACTS.
- **Dutch angle.** `tilt` below is validated against synthetic rotations to
  within ~3 degrees, but dominant structure orientation in an anime close-up is
  the subject's hood, hair and jawline rather than the camera's roll. It is
  measured and recorded, never proposed as a label.

  **Hough long-line detection was the documented next attempt. It was built on
  2026-07-26 and it fails too.** A paired rotation test puts it within 0.1-1.1
  degrees of an applied rotation across four titles -- far better than this
  measure's 13-16 -- and it still flags level frames, because parallel lines
  receding from the camera project to diagonals. What it fired on was an open
  book's pages, a receding rooftop railing, floorboards and power lines.
  Restricting to near-vertical lines helps and leaves the vertical-convergence
  case (low-angle shots). **Line orientation cannot separate camera roll from
  perspective without a horizon or vanishing-point estimate, so do not build a
  third orientation-based measure.** See FACTS.

Nothing consumes `tilt_degrees` or `tilt_coherence`. They are recorded as
evidence and no label reads them; that is deliberate, not an oversight.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

# Width matched to QUALITY_GRID; height follows the source so gradients stay
# isotropic. The fixed 16:9 quality grid biases `detail` by up to 18% across the
# aspect ratios seen -- orientation is far more sensitive to that than a magnitude
# is, so this one tracks the real aspect.
GRID_WIDTH = 256

# A frame is symmetrical when its mirror is nearly itself. Measured over the
# 152 stills of one film: every frame at or above this was a genuine symmetrical composition
# -- the subway corridor, the one-point-perspective concourse, the pillared
# hall, the escalator. Expect the very top of the range to be title cards; the
# fusion step is where they are excluded, using the tagger.
SYMMETRY_THRESHOLD = 0.95


@dataclass(frozen=True)
class CompositionGeometry:
    """Measurements only. No verdict -- that needs evidence from elsewhere."""

    symmetry: float
    tilt_degrees: float
    tilt_coherence: float

    def to_dict(self) -> dict:
        return {
            "symmetry": round(self.symmetry, 6),
            # Recorded for review and for a future attempt, NOT for labelling.
            "tilt_degrees": round(self.tilt_degrees, 3),
            "tilt_coherence": round(self.tilt_coherence, 6),
            # The threshold belongs with the measurement, so consumers read a
            # fact instead of re-deriving it across a worker boundary. Whether
            # to SAY "symmetrical" is a separate question and needs the tagger.
            "symmetrical": self.is_symmetrical,
        }

    @property
    def is_symmetrical(self) -> bool:
        return self.symmetry >= SYMMETRY_THRESHOLD


def measure(png_bytes: bytes) -> CompositionGeometry:
    """Decode once and derive the geometry that needs no model."""
    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(png_bytes))
    image.load()
    height = max(1, round(GRID_WIDTH * image.height / image.width))
    luma = np.asarray(
        image.convert("L").resize((GRID_WIDTH, height), Image.BILINEAR),
        dtype=np.float64,
    ) / 255.0

    # Mirror about the vertical axis. The grid's aspect cannot affect this and
    # that is structural: resampling is separable, so the vertical resample
    # treats column x and column W-1-x identically and the mirror relationship
    # survives it exactly.
    symmetry = 1.0 - float(np.abs(luma - luma[:, ::-1]).mean())

    gy, gx = np.gradient(luma)
    magnitude = np.hypot(gx, gy)
    tilt, coherence = _tilt(gx, gy, magnitude)
    return CompositionGeometry(symmetry, tilt, coherence)


def _tilt(gx: Any, gy: Any, magnitude: Any) -> tuple[float, float]:
    """Frame tilt in degrees and how coherent it is, by circular statistics.

    Edge orientation is 180-periodic but *frame* tilt is 90-periodic: a scene's
    horizontals and verticals rotate together, so an upright frame has structure
    at 0 and 90 and a 15-degree tilt has it at 15 and 105. Mapping theta to
    4*theta sends both members of each pair to the same direction so they
    reinforce instead of cancelling; the mean resultant then gives the tilt and
    its length gives the coherence.

    Validated against synthetic rotations of a real frame: 5 degrees reads 6.3,
    10 reads 11.8, 20 reads 23.1, 30 reads 33.1.
    """
    import numpy as np

    strong = magnitude > np.percentile(magnitude, 80)
    if strong.sum() < 32:
        return 0.0, 0.0
    theta = np.arctan2(gy[strong], gx[strong]) + np.pi / 2.0
    weight = magnitude[strong]
    resultant = np.sum(weight * np.exp(4j * theta)) / weight.sum()
    tilt = np.degrees(np.angle(resultant)) / 4.0
    # Fold into -45..45; a 46-degree tilt is a -44-degree tilt.
    return float((tilt + 45.0) % 90.0 - 45.0), float(np.abs(resultant))
