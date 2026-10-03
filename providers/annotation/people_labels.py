"""Fuse face boxes with tagger count hints into a people bucket.

research/02: "A face count is not a person count. It misses profiles, rear
views, masks, occlusion, distant full bodies, non-human characters, and can
count posters, screens, reflections, or photographs. Fuse face boxes, anime
tagger group/count hints, adjacent-frame consistency, and optional future person
segmentation. Publish the bucket rather than an invented exact number."

Faces are treated as a floor, never a ceiling. That is the one thing the first
real run established beyond doubt: on 16 frames the detector never exceeded the
true count and frequently sat under it -- zero on back-turned and hooded
figures, zero on a distant plaza crowd. A rule that let a face count pull an
estimate DOWN would therefore be wrong by construction.

Which fusion is best is NOT settled. Every rule tried -- faces alone, faces as a
floor, tagger unless faces exceed it -- scored identically on that sample,
because faces never exceeded the tagger there. Choosing between them needs more
frames than one title's worth. Until then this takes the conservative rule that
follows from the lower-bound property and flags every disagreement for review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .wd_labels import WdLabels, _bucket

BUCKET_FLOOR = {"zero": 0, "one": 1, "two": 2, "small-group": 3, "crowd": 6}


@dataclass(frozen=True)
class PeopleVerdict:
    value: str
    score: float
    face_count: int
    tagger_value: str
    evidence_inconsistent: bool


def fuse(
    wd: Optional[WdLabels],
    face_count: Optional[int],
    face_confidence: float = 0.7,
) -> PeopleVerdict:
    """Combine the two count signals into one bucket.

    Disagreement is reported rather than hidden. The protocol lists
    "count evidence is inconsistent" as a review trigger, so a split between the
    two providers must reach the reviewer as a lowered score instead of being
    averaged into false confidence.
    """
    tagger_value = wd.value("people") if wd is not None else "abstain"
    tagger_score = wd.score("people") if wd is not None else 0.0

    if face_count is None:
        return PeopleVerdict(tagger_value, tagger_score, 0, tagger_value, False)

    face_value = _bucket(face_count)
    if tagger_value == "abstain":
        return PeopleVerdict(face_value, round(face_confidence, 4), face_count,
                             "abstain", False)

    tagger_floor = BUCKET_FLOOR.get(tagger_value, 0)
    fused = _bucket(max(face_count, tagger_floor))
    inconsistent = face_value != tagger_value
    score = min(tagger_score, face_confidence) if inconsistent else max(
        tagger_score, face_confidence
    )
    if inconsistent:
        score *= 0.7
    return PeopleVerdict(fused, round(score, 4), face_count, tagger_value,
                         inconsistent)
