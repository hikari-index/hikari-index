"""Decide `shot_scale` from whichever evidence a frame actually carries.

The tagger names a scale on 11-20% of published frames, which is not enough to
review against: you cannot ask whether a label is right when there is no label.
This resolves the field from three independent lanes instead of one.

The lanes are deliberately not a vote. Each one either applies to a frame or
does not, they are tried in descending order of trust, and the first that
applies answers. Every result names the lane that produced it, so the reviewed sample
can score each lane separately rather than scoring an average that hides which
signal was wrong.

Why not face size alone: it cannot see a frame without a face, and on two real
episodes 38-54% of published frames have none. Face occupancy is a strong
signal inside its domain and blind outside it, which is exactly the shape a
lane should have.

Nothing here guesses. A frame that no lane reaches abstains, and on the
measured episodes that is 17-26% of frames -- mostly people shot from behind or
too far away for a face. Those need a signal that does not exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import taxonomy

#: Face area as a fraction of frame area. Calibrated 2026-07-26 against the
#: 53 face-lane frames of the reviewed sample (human verdicts):
#:
#:     human said        observed fraction range
#:     close-up          0.1556 - 0.5315
#:     medium            0.0088 - 0.1126
#:     wide              0.0007 - 0.0087
#:
#: The extreme classes are NOT separable by face fraction and the lane no
#: longer asserts them. Human extreme-close-ups were observed at 0.4731 AND
#: 0.5591 while close-ups reached 0.5315 -- the classes overlap in 0.41-0.56,
#: and 5 of the 7 frames the old 0.40 cut called extreme-close-up were
#: corrected to close-up. The one extreme-wide assertion (0.0007) was also
#: corrected, to wide. On the calibration sample these cuts score 48/53
#: against the old bounds' 44/53, and every remaining miss is an adjacent
#: class. No lane asserts the extremes today: the allowlist maps no tagger tag
#: to `extreme-close-up` or `extreme-wide`, and the scenery lane answers
#: `wide`. Making either reachable needs a tag in the checkpoint's vocabulary
#: that names it, checked against reviewed frames.
FACE_CUTS = (
    (0.14, "close-up"),
    (0.009, "medium"),
)
FACE_FLOOR_VALUE = "wide"

#: A lane that reaches a value it cannot defend precisely still routes to a
#: human. These sit below the protocol's confident band on purpose.
FACE_BASE_SCORE = 0.55
FACE_EDGE_SCORE = 0.35
SCENERY_SCORE = 0.30

#: How close to a cut point counts as "near it", in log space, since the cuts
#: span three orders of magnitude and a fixed absolute margin would be
#: meaningless at both ends.
NEAR_CUT_DECADES = 0.15


@dataclass(frozen=True)
class ShotScale:
    value: str
    score: float
    lane: str

    @property
    def backed(self) -> bool:
        return self.value not in ("abstain", "unknown")


ABSTAIN = ShotScale(value="abstain", score=0.0, lane="none")


def _log10(value: float) -> float:
    from math import log10

    return log10(max(value, 1e-9))


def from_face_occupancy(fraction: float) -> ShotScale:
    """Largest face area as a share of the frame, mapped to a scale.

    A face near a cut point scores lower than one in the middle of a band. The
    bands span three orders of magnitude, so nearness is measured in decades:
    an absolute margin would be enormous at the close-up end and invisible at
    the wide end.
    """
    if fraction <= 0.0:
        return ABSTAIN
    value = FACE_FLOOR_VALUE
    for cut, label in FACE_CUTS:
        if fraction >= cut:
            value = label
            break
    near = any(abs(_log10(fraction) - _log10(cut)) < NEAR_CUT_DECADES
               for cut, _ in FACE_CUTS)
    score = FACE_EDGE_SCORE if near else FACE_BASE_SCORE
    return ShotScale(value=taxonomy.check("shot_scale", value),
                     score=score, lane="face-occupancy")


def from_scenery(scenery_tagged: bool) -> ShotScale:
    """A frame the tagger calls scenery is a landscape, and landscapes are wide.

    Requires the `scenery` tag specifically, not `no_humans`. "No people in
    frame" says nothing about scale -- a close-up of a teacup has no people in
    it -- and treating the two as equivalent would label every object shot as
    wide. Measured over the frames no other lane reached, `scenery` fires on
    roughly a third of them and `no_humans` on far more, which is the point:
    the broader tag is broader because it is answering a different question.

    Scores low and lands in the review band. `scenery` has no allowlist
    mapping: it says what a frame shows, not how far away the camera is, so it
    is too weak to corroborate a tagger verdict. Letting it decide here is a
    deliberate relaxation for frames that would otherwise say nothing, not a
    promotion.

    KNOWN FAILURE, observed not hypothesised: a close-up of a natural subject is
    scenery and is not wide. A macro shot of wheat heads against the sky was
    labelled `wide` here. Its tags -- `no_humans`, `sky`, `outdoors`, `scenery`,
    `wheat`, `field` -- are indistinguishable from those of a wheat field shot
    from a distance, and the depth-of-field cues that would separate them fired
    on 2 of 54 frames across both episodes. There is no guard available at this
    layer, so the lane keeps its low score and its review disposition and the
    reviewed sample is left to measure how often this happens. Do not add a tag-based
    guard for it without evidence that one works.
    """
    if not scenery_tagged:
        return ABSTAIN
    return ShotScale(value=taxonomy.check("shot_scale", "wide"),
                     score=SCENERY_SCORE, lane="scenery")


def resolve(
    tagger_value: str,
    tagger_score: float,
    face_fraction: Optional[float] = None,
    scenery_tagged: bool = False,
) -> ShotScale:
    """First lane that applies, in descending order of trust.

    The tagger goes first because it read the frame and named the scale
    directly. Face occupancy goes second because it is a measurement rather
    than a naming, and it is only valid where a face exists. Scenery goes last
    because it infers scale from subject matter, which is the weakest of the
    three.
    """
    if tagger_value not in ("abstain", "unknown"):
        return ShotScale(value=tagger_value, score=tagger_score, lane="tagger")
    if face_fraction:
        by_face = from_face_occupancy(face_fraction)
        if by_face.backed:
            return by_face
    return from_scenery(scenery_tagged)
