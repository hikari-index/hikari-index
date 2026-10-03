"""Turn WD tagger predictions into taxonomy values through a closed allowlist.

The tagger's vocabulary is fan-site vocabulary, not a product taxonomy: 10,861
tags of which 2,751 are named characters and the bulk of the rest describe who is
in frame rather than how it is shot. This module is the boundary where that gets
culled. A tag reaches a record only by appearing in `wd_allowlist.json` with a
taxonomy value to land on; everything else is discarded by default.

Matching is exact. Substring matching on this vocabulary is actively dangerous --
"bloom" catches `bloomers`, "shadow" catches `eyeshadow`, and "dark" catches
`dark_skin` and `dark-skinned_female`, which are demographic descriptors the
protocol forbids. There is no pattern path through this module.

Count tags are read for arity only. The frequent count vocabulary is inherently
gendered, so `1girl` and `1boy` both yield the people value `one` and the gender
assertion is dropped here, permanently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from . import taxonomy

ALLOWLIST_PATH = Path(__file__).with_name("wd_allowlist.json")

# A corroborating tag agrees with a primary one but never decides a value alone.
PRIMARY = "primary"
CORROBORATING = "corroborating"


@dataclass(frozen=True)
class Mapping:
    tag: str
    family: str
    field: str
    value: str
    role: str


@dataclass(frozen=True)
class Allowlist:
    version: str
    mappings: dict[str, Mapping]
    text_evidence: frozenset[str]
    # Content facets: multi-value open-vocabulary evidence lists. A tag here
    # reaches the record verbatim rather than landing on a taxonomy value, so
    # membership IS the whole rule -- same exact-name discipline as mappings.
    things: frozenset[str] = frozenset()
    actions: frozenset[str] = frozenset()

    def lookup(self, tag: str) -> Optional[Mapping]:
        """Exact match only. Never call this with a pattern or a prefix."""
        return self.mappings.get(tag)


def load(path: Path = ALLOWLIST_PATH) -> Allowlist:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document["taxonomy_version"] != taxonomy.TAXONOMY_VERSION:
        raise ValueError(
            f"allowlist targets {document['taxonomy_version']}, "
            f"code expects {taxonomy.TAXONOMY_VERSION}"
        )
    mappings: dict[str, Mapping] = {}
    for entry in document["mappings"]:
        taxonomy.check(entry["field"], entry["value"])
        if entry["role"] not in (PRIMARY, CORROBORATING):
            raise ValueError(f"{entry['tag']!r} has an unknown role {entry['role']!r}")
        if entry["tag"] in mappings:
            raise ValueError(f"{entry['tag']!r} is mapped twice")
        mappings[entry["tag"]] = Mapping(**entry)
    facets = document["content_facets"]
    return Allowlist(
        version=document["allowlist_version"],
        mappings=mappings,
        text_evidence=frozenset(document["text_evidence"]["tags"]),
        things=frozenset(facets["things"]),
        actions=frozenset(facets["actions"]),
    )


@dataclass(frozen=True)
class FieldVerdict:
    value: str
    score: float
    supporting_tags: tuple[str, ...]


def _resolve_field(
    candidates: list[tuple[Mapping, float]],
) -> Optional[FieldVerdict]:
    """Pick one value for a field from the tags that survived the allowlist.

    A corroborating tag cannot win on its own -- `portrait` may imply a close-up,
    but only alongside something that names the value directly. Where two primary
    tags disagree the higher-scoring one wins and the score is damped by the
    disagreement, so a split decision routes to review rather than reading as
    confident.
    """
    if not candidates:
        return None
    by_value: dict[str, list[tuple[Mapping, float]]] = {}
    for mapping, score in candidates:
        by_value.setdefault(mapping.value, []).append((mapping, score))

    scored: list[tuple[float, str, tuple[str, ...]]] = []
    for value, entries in by_value.items():
        has_primary = any(m.role == PRIMARY for m, _ in entries)
        if not has_primary:
            continue
        best = max(score for _, score in entries)
        corroboration = sum(0.05 for m, _ in entries if m.role == CORROBORATING)
        tags = tuple(sorted(m.tag for m, _ in entries))
        scored.append((min(1.0, best + corroboration), value, tags))
    if not scored:
        return None
    scored.sort(reverse=True)
    top_score, top_value, top_tags = scored[0]
    if len(scored) > 1:
        runner_up = scored[1][0]
        top_score = max(0.0, top_score - runner_up)
    return FieldVerdict(value=top_value, score=round(top_score, 4),
                        supporting_tags=top_tags)


@dataclass(frozen=True)
class WdLabels:
    fields: dict[str, FieldVerdict]
    text_present: bool
    discarded_tags: int
    kept_tags: int
    # Content facets, highest score first. Tag names verbatim from the
    # allowlist's facet lists; empty tuples when nothing fired.
    things: tuple[tuple[str, float], ...] = ()
    actions: tuple[tuple[str, float], ...] = ()

    def value(self, field: str) -> str:
        verdict = self.fields.get(field)
        return verdict.value if verdict else "abstain"

    def score(self, field: str) -> float:
        verdict = self.fields.get(field)
        return verdict.score if verdict else 0.0


#: Fields whose cut was human-validated at the model card's P=R point on the
#: reviewed sample (2026-07-26): values newly proposed between 0.2653
#: and 0.35 were accepted at 90/81/100%. Every other field keeps the base cut
#: -- for shot_scale deliberately, because a lower tagger cut overrides the
#: validated face-occupancy lane (40 of 41 flips in the 0.2653 re-fusion).
SCENE_FIELDS = ("setting", "time", "weather")
DEFAULT_SCENE_THRESHOLD = 0.2653


def from_predictions(
    predictions: dict[str, float],
    allowlist: Allowlist,
    threshold: float = 0.35,
    scene_threshold: Optional[float] = None,
) -> WdLabels:
    """Map thresholded tagger predictions onto taxonomy fields.

    `threshold` is the base cut; `scene_threshold` (default: the same value)
    applies to SCENE_FIELDS only. The protocol still requires per-tag
    calibration on the reviewed sample; two cuts are a coarser version of that, not
    its replacement. A scene cut below the tags manifest's retention floor is
    silently inert -- the sub-floor scores were never stored.
    """
    if scene_threshold is None:
        scene_threshold = threshold
    by_field: dict[str, list[tuple[Mapping, float]]] = {}
    kept_names: set[str] = set()
    text_present = False
    for tag, score in predictions.items():
        if score >= threshold and tag in allowlist.text_evidence:
            text_present = True
        mapping = allowlist.lookup(tag)
        if mapping is None:
            continue
        cut = scene_threshold if mapping.field in SCENE_FIELDS else threshold
        if score < cut:
            continue
        kept_names.add(tag)
        by_field.setdefault(mapping.field, []).append((mapping, score))

    fields = {}
    for field, candidates in by_field.items():
        if field == "people":
            continue  # additive semantics; see _resolve_people
        verdict = _resolve_field(candidates)
        if verdict is not None:
            fields[field] = verdict
    people = _resolve_people(predictions, threshold)
    if people is not None:
        fields["people"] = people
    _derive_cross_field(fields, predictions, scene_threshold)

    def facet(names: frozenset[str]) -> tuple[tuple[str, float], ...]:
        fired = [(tag, round(score, 4)) for tag, score in predictions.items()
                 if tag in names and score >= threshold]
        kept_names.update(tag for tag, _ in fired)
        return tuple(sorted(fired, key=lambda item: (-item[1], item[0])))

    things = facet(allowlist.things)
    actions = facet(allowlist.actions)
    return WdLabels(
        fields=fields,
        text_present=text_present,
        discarded_tags=len(predictions) - len(kept_names),
        kept_tags=len(kept_names),
        things=things,
        actions=actions,
    )


# Count tags are ADDITIVE across gender, not alternatives. `2girls` and `1boy`
# firing together means three people, not a contest between two and one. The
# generic resolver treats co-firing tags as rival values, which undercounted
# every mixed group on the first real run. Within one gender the tags are
# mutually exclusive, so the largest wins there.
_GIRL_COUNTS = {"1girl": 1, "2girls": 2, "3girls": 3, "4girls": 4, "5girls": 5, "6+girls": 6}
_BOY_COUNTS = {"1boy": 1, "2boys": 2, "3boys": 3, "6+boys": 6}


def _bucket(total: int) -> str:
    if total <= 0:
        return "zero"
    if total == 1:
        return "one"
    if total == 2:
        return "two"
    if total <= 5:
        return "small-group"
    return "crowd"


def _resolve_people(
    predictions: dict[str, float], threshold: float
) -> Optional[FieldVerdict]:
    """Count people by summing gendered arity tags, then bucket the total.

    Gender is read only to know which counter a tag belongs to and is discarded
    immediately; `1girl` and `1boy` contribute an identical 1.
    """
    fired = {tag: score for tag, score in predictions.items() if score >= threshold}

    if "no_humans" in fired:
        return FieldVerdict("zero", round(fired["no_humans"], 4), ("no_humans",))
    if "crowd" in fired:
        return FieldVerdict("crowd", round(fired["crowd"], 4), ("crowd",))
    # `solo` asserts a total of one outright, so it settles the field rather
    # than contributing to a sum.
    if "solo" in fired:
        return FieldVerdict("one", round(fired["solo"], 4), ("solo",))

    girls = [(t, c) for t, c in _GIRL_COUNTS.items() if t in fired]
    boys = [(t, c) for t, c in _BOY_COUNTS.items() if t in fired]
    if not girls and not boys:
        if "couple" in fired:
            return FieldVerdict("two", round(fired["couple"] * 0.8, 4), ("couple",))
        return None

    total = max((c for _, c in girls), default=0) + max((c for _, c in boys), default=0)
    tags = tuple(sorted(t for t, _ in girls + boys))
    score = min(fired[t] for t in tags)
    # A mixed group is a sum of two independent predictions, so it is less
    # certain than a single tag carrying the whole count.
    if girls and boys:
        score *= 0.85
    return FieldVerdict(_bucket(total), round(score, 4), tags)


def _derive_cross_field(
    fields: dict[str, FieldVerdict],
    predictions: dict[str, float],
    threshold: float,
) -> None:
    """Fill taxonomy values no single tag names but the evidence implies.

    Two values in the coarse taxonomy have no tag of their own yet follow
    directly from tags that do exist. Deriving them is not inference beyond the
    evidence: both are what the tags already say, read together.
    """
    indoors = predictions.get("indoors", 0.0)
    outdoors = predictions.get("outdoors", 0.0)

    # Both sides firing is what `mixed` means -- an interior open to outside.
    # Without this the resolver picks a winner and reports a damped score, which
    # loses a value the taxonomy carries.
    if indoors >= threshold and outdoors >= threshold:
        fields["setting"] = FieldVerdict(
            value="mixed",
            score=round(min(indoors, outdoors), 4),
            supporting_tags=("indoors", "outdoors"),
        )

    # Weather is not visible from inside. Only claim this where the setting is
    # confidently interior and no weather tag fired at all -- rain through a
    # window is real weather evidence and must survive.
    setting = fields.get("setting")
    if (
        "weather" not in fields
        and setting is not None
        and setting.value == "interior"
    ):
        fields["weather"] = FieldVerdict(
            value="none-visible",
            score=round(setting.score * 0.8, 4),
            supporting_tags=setting.supporting_tags,
        )


def cull_report(predictions: Iterable[str], allowlist: Allowlist) -> dict:
    """What the allowlist removed, for run evidence."""
    tags = list(predictions)
    kept = [t for t in tags if allowlist.lookup(t) is not None]
    return {
        "allowlist_version": allowlist.version,
        "tags_seen": len(tags),
        "tags_mapped": len(kept),
        "tags_discarded": len(tags) - len(kept),
        "mapped_tags": sorted(kept),
    }
