"""Resolve the `composition` family from geometry plus inference evidence.

`research/02` prescribes composition as spatial rules PLUS controlled zero-shot.
The zero-shot half scored 0.532 against a 0.554 majority baseline over 3,190
frames and is dead. This assembles the other half.

The geometry is measured on the host-local worker (`frame_sample.composition`)
because it needs no model. The verdict is here because it needs two things the
worker does not have: the tagger, to tell a symmetric title card from a
symmetric composition, and the face boxes, for subject position.

Measured coverage on three episodes, with the card rule applied: 50%, 48%
and 33%. Everything else abstains, which is a legal value
and an honest one -- see the module notes on what was tried and rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

# Thirds lines sit at 1/3 and 2/3; a subject within this of one is "on" it, and
# within this of the middle is centred. research/02 calls these "candidates"
# precisely because geometry does not prove cinematic intent, so they reach the
# reviewer as proposals rather than facts.
THIRDS_TOLERANCE = 0.06
CENTRE_TOLERANCE = 0.06

# `WdLabels.text_present` plus entropy below this is a text-only card: type on a flat
# field with nothing under it. Measured over 60 text-tagged frames with every
# band read by eye -- cards run from 0.63 to 3.26 and content-bearing text
# starts near 3.4. Above the cut, cards on textured grounds are indistinguishable
# from diegetic text, which research/01 forbids removing, so only the low side
# is acted on.
CARD_MAX_ENTROPY = 3.0

# Confidence for a geometry-derived proposal. Deliberately below the palette
# families: this is a measurement of the picture, not an assertion about intent.
GEOMETRY_SCORE = 0.6
# The same rules from a head box where no face was found (see resolve).
HEAD_SCORE = 0.5


@dataclass(frozen=True)
class CompositionVerdict:
    value: str
    score: float
    basis: str


def _subject_x(faces: Optional[Sequence[dict]], width: int) -> Optional[float]:
    """Horizontal centre of the LARGEST box (a face, or a head), in 0..1.

    Not the area-weighted centroid of every face: on a two-shot that lands in
    the gap between two subjects and reads as `centered` when neither subject is
    near the centre. research/02 puts two-shot and group in the *people* family;
    composition answers where the dominant subject sits.
    """
    if not faces or width <= 0:
        return None
    best, best_area = None, 0.0
    for face in faces:
        box = face.get("box") or []
        if len(box) != 4:
            continue
        x0, y0, x1, y1 = box
        area = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        if area > best_area:
            best, best_area = box, area
    if best is None or best_area <= 0:
        return None
    return (best[0] + best[2]) / 2.0 / width


def resolve(
    symmetry: Optional[float] = None,
    is_symmetrical: bool = False,
    entropy: Optional[float] = None,
    text_present: bool = False,
    faces: Optional[Sequence[dict]] = None,
    frame_width: int = 0,
    heads: Optional[Sequence[dict]] = None,
) -> CompositionVerdict:
    """One composition value, or `abstain`.

    Order matters. The card test runs first because cards top the symmetry
    range -- type centred on a flat field mirrors almost perfectly, and without
    this gate symmetry is a card detector on Shaft titles, where 63% of its hits
    carried a typography tag. An interstitial has no cinematographic composition
    to describe.
    """
    if text_present and entropy is not None and entropy < CARD_MAX_ENTROPY:
        return CompositionVerdict("abstain", 0.0, "text-only-card")

    if is_symmetrical:
        return CompositionVerdict("symmetrical", GEOMETRY_SCORE, "mirror")

    x, score, basis = _subject_x(faces, frame_width), GEOMETRY_SCORE, "face-position"
    if x is None:
        # No face: the largest head, from the anime head detector, which sees
        # the back of a head and a profile. Its centre tracks the face centre
        # at Pearson 0.981 on the 852 gold-set frames that have both (the best
        # non-model estimator reached 0.689 and lost), and these rules give
        # the same value from either on 82% of them; the rest sit near a
        # tolerance edge. Scored below the face rule for that.
        x, score, basis = _subject_x(heads, frame_width), HEAD_SCORE, "head-position"
    if x is None:
        # No face and no head means no subject position. Four estimators were
        # measured against face-box position and the best gained +2.5pp over
        # the majority baseline on the hardest title while labelling title
        # cards `centered`. Abstaining is the honest answer, not a placeholder.
        return CompositionVerdict("abstain", 0.0, "no-subject-signal")

    if abs(x - 0.5) <= CENTRE_TOLERANCE:
        return CompositionVerdict("centered", score, basis)
    if min(abs(x - 1 / 3), abs(x - 2 / 3)) <= THIRDS_TOLERANCE:
        return CompositionVerdict("rule-of-thirds", score, basis)
    return CompositionVerdict("asymmetrical", score, basis)
