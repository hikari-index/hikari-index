"""Assemble one model proposal for one candidate.

Every proposal carries all six label families, so a provider covering
three of them cannot emit anything on its own. This module is the join: it
collects whatever the available providers support and fills the rest with
`abstain`, producing a proposal that validates today and gains real values as
providers arrive.

Abstention is not a placeholder to be tidied away later. The protocol demands an
abstain state, and a proposal that guessed at families it has no evidence for
would corrupt review-by-exception -- a reviewer accepting a plausible-looking
default is worse than one correcting an obvious blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from . import taxonomy
from .palette_labels import PaletteLabels
from .people_labels import fuse as fuse_people
from .composition_labels import resolve as resolve_composition
from .shot_scale import resolve as resolve_scale
from .wd_labels import WdLabels

PROVIDER_VERSION = "0.1.0"

# Mirrors $defs/opaqueId in annotation-record.schema.json. Enforced here so an
# unusable id fails at construction rather than at review-time validation.
OPAQUE_ID = re.compile(r"^[a-z]+-[a-z0-9][a-z0-9-]{2,63}$")

# Published candidates have already cleared the pipeline's quality floor and its
# redundancy test, so the only quality values still in play are the ones nothing
# measures yet: text cards and text overlays. The proposal says `usable` because
# that is what the evidence supports, and carries a deliberately low score so
# the protocol's near-threshold rule routes the family to human review instead
# of letting a confident-looking default pass unread.
QUALITY_USABLE_SCORE = 0.35


@dataclass
class CandidateEvidence:
    candidate_id: str
    shot_id: str
    palette: PaletteLabels
    wd: Optional["WdLabels"] = None
    face_count: Optional[int] = None
    face_fraction: Optional[float] = None
    scenery_tagged: bool = False
    # Composition evidence. Geometry from the host-local worker, faces and tags
    # from the inference worker; the verdict needs both, which is why it is
    # resolved here and not on either one alone.
    symmetry: Optional[float] = None
    is_symmetrical: bool = False
    entropy: Optional[float] = None
    faces: tuple = ()
    frame_width: int = 0
    # Head boxes from the anime head detector (inference.detect_figures),
    # largest first, and the largest one's height as a share of the frame's.
    # Used only where no face answered.
    heads: tuple = ()
    head_height: Optional[float] = None
    quality_labels: tuple[str, ...] = ("usable",)
    quality_disposition: str = "review"
    quality_score: float = QUALITY_USABLE_SCORE
    notes: list[str] = field(default_factory=list)


def _composition(evidence: CandidateEvidence):
    """Geometry plus tagger plus faces. See composition_labels for the rules."""
    return resolve_composition(
        symmetry=evidence.symmetry,
        is_symmetrical=evidence.is_symmetrical,
        entropy=evidence.entropy,
        text_present=evidence.wd.text_present if evidence.wd is not None else False,
        faces=evidence.faces,
        frame_width=evidence.frame_width,
        heads=evidence.heads,
    )


def _shot_scale(evidence: CandidateEvidence):
    wd = evidence.wd
    return resolve_scale(
        wd.value("shot_scale") if wd else "abstain",
        wd.score("shot_scale") if wd else 0.0,
        face_fraction=evidence.face_fraction,
        scenery_tagged=evidence.scenery_tagged,
        head_height=evidence.head_height,
    )


def shot_scale_lane(evidence: CandidateEvidence) -> str:
    """Which lane answered shot_scale, for per-lane calibration."""
    return _shot_scale(evidence).lane


NO_SOURCE = {"source": "none", "score": 0.0}


def field_sources(evidence: CandidateEvidence) -> dict[str, dict]:
    """Which signal answered each label field, and with what score.

    Recorded beside the proposal, like the shot-scale lane, so review can be
    scored per label, per source and per value (an accuracy figure over a
    fusion says nothing about which signal to fix), and so a cut-off can be
    re-tuned from reviewed frames. A field that abstained has source `none`.
    Must agree with build_labels: the value a source is named for here is
    the value the proposal carries.
    """
    wd = evidence.wd
    palette = evidence.palette

    def entry(source: str, score: float) -> dict:
        return {"source": source, "score": round(float(score), 4)}

    out: dict[str, dict] = {}
    for name in ("setting", "time", "weather", "angle"):
        if wd is None or wd.value(name) == "abstain":
            out[name] = dict(NO_SOURCE)
        elif name == "weather" and wd.value(name) == "none-visible":
            # Inferred from a confident interior, not a weather tag.
            out[name] = entry("interior-rule", wd.score(name))
        else:
            out[name] = entry("tagger", wd.score(name))

    scale = _shot_scale(evidence)
    out["shot_scale"] = (entry(scale.lane, scale.score)
                         if scale.value != "abstain" else dict(NO_SOURCE))

    # Without the tagger the card test cannot run, so composition abstains
    # (build_labels via _tagger_fields).
    composition = _composition(evidence) if wd is not None else None
    out["composition"] = (entry(composition.basis, composition.score)
                          if composition and composition.value != "abstain"
                          else dict(NO_SOURCE))

    people = fuse_people(wd, evidence.face_count)
    if people.value == "abstain":
        out["people"] = dict(NO_SOURCE)
    elif evidence.face_count is None or people.tagger_value == people.value:
        # The tagger's count stands (faces absent, or not above it).
        both = (evidence.face_count is not None
                and not people.evidence_inconsistent)
        out["people"] = entry("tagger+faces" if both else "tagger", people.score)
    else:
        # The face count decided: the tagger abstained or counted fewer.
        out["people"] = entry("faces", people.score)

    lighting, color_bias = _lighting_and_bias(evidence)
    if lighting == "abstain":
        out["lighting"] = dict(NO_SOURCE)
    elif wd is not None and wd.value("lighting") == lighting:
        out["lighting"] = entry("tagger", wd.score("lighting"))
    else:
        out["lighting"] = entry("palette", palette.lighting_score)
    if color_bias == "abstain":
        out["color_bias"] = dict(NO_SOURCE)
    elif wd is not None and color_bias == "monochrome" and wd.value("color_bias") == "monochrome":
        out["color_bias"] = entry("tagger", wd.score("color_bias"))
    else:
        out["color_bias"] = entry("palette", palette.color_bias_score)
    out["saturation"] = (entry("palette", palette.saturation_score)
                         if palette.saturation != "abstain" else dict(NO_SOURCE))
    return out


def _tagger_fields(evidence: CandidateEvidence) -> dict:
    """Families the tagger can reach, abstaining where it said nothing.

    `composition` has no tagger route at all and waits on spatial rules.
    `people` takes the tagger's count hint as an interim value: research/02 wants
    face boxes fused with these hints into a bucket, and until the face detector
    exists a hint alone is weaker than the eventual answer but better than an
    abstention that tells a reviewer nothing.
    """
    wd = evidence.wd
    if wd is None:
        gaps = {name: "abstain" for name in
                ("setting", "time", "weather", "shot_scale", "angle",
                 "composition", "people")}
        gaps["people"] = fuse_people(None, evidence.face_count).value
        gaps["shot_scale"] = _shot_scale(evidence).value
        return gaps
    return {
        "setting": wd.value("setting"),
        "time": wd.value("time"),
        "weather": wd.value("weather"),
        "shot_scale": _shot_scale(evidence).value,
        "angle": wd.value("angle"),
        "composition": _composition(evidence).value,
        "people": fuse_people(wd, evidence.face_count).value,
    }


def _lighting_and_bias(evidence: CandidateEvidence) -> tuple[str, str]:
    """Reconcile the two providers that both reach this family.

    The tagger wins where it speaks. `backlit` and `silhouette` describe lighting
    character the colour descriptor structurally cannot reach, and a direct
    `monochrome` assertion beats a saturation threshold that no frame in the
    calibration sample ever triggered. The colour descriptor carries the field
    otherwise, which is most of the time.
    """
    palette = evidence.palette
    lighting, color_bias = palette.lighting, palette.color_bias
    if evidence.wd is not None:
        tagged_lighting = evidence.wd.value("lighting")
        if tagged_lighting != "abstain":
            lighting = tagged_lighting
        if evidence.wd.value("color_bias") == "monochrome":
            color_bias = "monochrome"
    return lighting, color_bias


def build_labels(evidence: CandidateEvidence) -> dict:
    gaps = _tagger_fields(evidence)
    palette = evidence.palette
    lighting, color_bias = _lighting_and_bias(evidence)
    labels = {
        "setting_time_weather": {
            "setting": taxonomy.check("setting", gaps["setting"]),
            "time": taxonomy.check("time", gaps["time"]),
            "weather": taxonomy.check("weather", gaps["weather"]),
        },
        "lighting_color_character": {
            "lighting": taxonomy.check("lighting", lighting),
            "color_bias": taxonomy.check("color_bias", color_bias),
            "saturation": taxonomy.check("saturation", palette.saturation),
            "palette_evidence": taxonomy.check(
                "palette_evidence", palette.palette_evidence
            ),
        },
        "shot_scale": taxonomy.check("shot_scale", gaps["shot_scale"]),
        "angle_composition": {
            "angle": taxonomy.check("angle", gaps["angle"]),
            "composition": taxonomy.check("composition", gaps["composition"]),
        },
        "people": taxonomy.check("people", gaps["people"]),
        # Content facets: allowlisted tag names verbatim, empty when the tagger
        # is absent or nothing fired. Multi-value evidence, not sealed labels --
        # the allowlist's facet lists are the closed vocabulary, so no
        # taxonomy.check applies here.
        "content": {
            "things": [tag for tag, _ in (evidence.wd.things if evidence.wd else ())],
            "actions": [tag for tag, _ in (evidence.wd.actions if evidence.wd else ())],
        },
        "quality": {
            "labels": list(evidence.quality_labels),
            "selection_disposition": evidence.quality_disposition,
        },
    }
    for value in labels["quality"]["labels"]:
        if value not in taxonomy.QUALITY:
            raise ValueError(f"{value!r} is not a quality value")
    if labels["quality"]["selection_disposition"] not in taxonomy.DISPOSITION:
        raise ValueError("invalid selection disposition")
    return labels


def build_scores(evidence: CandidateEvidence) -> list[dict]:
    """One entry per family, carrying that family's best-backed label.

    A family scores the mean of its backed sub-labels, ignoring the ones that
    abstained. Taking a minimum instead would let a single abstention -- say
    lighting on a frame whose colour is unambiguous -- report the whole family
    as unbacked, and coverage reporting would then understate what exists.
    """
    palette = evidence.palette
    wd = evidence.wd
    lighting, color_bias = _lighting_and_bias(evidence)
    backed = [
        (color_bias, max(palette.color_bias_score,
                         wd.score("color_bias") if wd else 0.0)),
        (palette.saturation, palette.saturation_score),
        (lighting, max(palette.lighting_score,
                       wd.score("lighting") if wd else 0.0)),
    ]
    backed = [(label, score) for label, score in backed if score > 0.0]
    if backed:
        headline, _ = max(backed, key=lambda item: item[1])
        group_score = sum(score for _, score in backed) / len(backed)
    else:
        headline, group_score = "abstain", 0.0

    def tagged(field: str) -> tuple[str, float]:
        if wd is None:
            return "abstain", 0.0
        return wd.value(field), wd.score(field)

    stw = max((tagged(f) for f in ("setting", "time", "weather")),
              key=lambda pair: pair[1])
    scale_verdict = _shot_scale(evidence)
    scale = (scale_verdict.value, scale_verdict.score)
    angle = tagged("angle")
    fused = fuse_people(wd, evidence.face_count)
    people = (fused.value, fused.score)
    return [
        {"family": "setting-time-weather", "label": stw[0], "score": round(stw[1], 4)},
        {"family": "lighting-color-character", "label": headline,
         "score": round(group_score, 4)},
        {"family": "shot-scale", "label": scale[0], "score": round(scale[1], 4)},
        {"family": "angle-composition", "label": angle[0], "score": round(angle[1], 4)},
        {"family": "people", "label": people[0], "score": round(people[1], 4)},
        {"family": "quality", "label": evidence.quality_labels[0],
         "score": round(evidence.quality_score, 4)},
    ]


def build_proposal(
    evidence: CandidateEvidence,
    provider_id: str,
    configuration_ref: str,
    run_ref: str,
) -> dict:
    for opaque in (provider_id, configuration_ref, run_ref):
        if not OPAQUE_ID.match(opaque or ""):
            raise ValueError(
                f"{opaque!r} is not an opaque id matching {OPAQUE_ID.pattern}"
            )
    proposal = {
        "provider_id": provider_id,
        "provider_version": PROVIDER_VERSION,
        "configuration_ref": configuration_ref,
        "run_ref": run_ref,
        "labels": build_labels(evidence),
        "scores": build_scores(evidence),
    }
    families = [entry["family"] for entry in proposal["scores"]]
    if sorted(families) != sorted(taxonomy.FAMILIES):
        raise ValueError("scores must carry exactly one entry per family")
    return proposal


def coverage(proposals: list[dict]) -> dict:
    """How much of the taxonomy is actually backed, for the run record."""
    backed: dict[str, int] = {}
    total = len(proposals) or 1
    for proposal in proposals:
        for entry in proposal["scores"]:
            if entry["score"] > 0.0:
                backed[entry["family"]] = backed.get(entry["family"], 0) + 1
    return {
        "families_total": len(taxonomy.FAMILIES),
        "families_backed": sorted(backed),
        "families_abstaining": sorted(set(taxonomy.FAMILIES) - set(backed)),
        "backed_fraction_by_family": {
            family: round(backed.get(family, 0) / total, 4)
            for family in taxonomy.FAMILIES
        },
    }
