"""Emit bulk model proposals for a prepared bundle and its colour results.

Reads only artifacts the pipeline already produces. Output is private: it is
derived from candidate pixels and carries the work's identity, so it belongs
beside the run, never in the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import taxonomy
from .shot_scale import HEAD_CUTS
from .palette_labels import from_descriptor
from .proposal import (CandidateEvidence, build_proposal, coverage,
                       field_sources, shot_scale_lane)
from .wd_labels import (DEFAULT_SCENE_THRESHOLD, from_predictions,
                        load as load_allowlist)


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as source:
        return json.load(source)


def _descriptor_for(results_root: Path, candidate_id: str) -> Optional[dict]:
    path = results_root / "artifacts" / f"{candidate_id}.descriptor.json"
    return _load(path) if path.is_file() else None


def _by_candidate(result_manifest: Optional[Path]) -> dict[str, dict]:
    """Index an inference result manifest by candidate_id, or empty if absent.

    Each provider is optional: a proposal is valid without it, that provider's
    families simply abstain. Only when a result IS supplied is it consumed.
    """
    if result_manifest is None or not result_manifest.is_file():
        return {}
    document = _load(result_manifest)
    return {entry["candidate_id"]: entry for entry in document.get("candidates", [])}


def emit(bundle: Optional[Path], results: Path, provider_id: str,
         configuration_ref: str, run_ref: str,
         tags: Optional[Path] = None, faces: Optional[Path] = None,
         coverage_audit: Optional[Path] = None,
         tag_threshold: float = 0.35,
         scene_tag_threshold: Optional[float] = None,
         candidates: Optional[Path] = None,
         figures: Optional[Path] = None) -> dict:
    """`candidates` stands in for the bundle manifest: JSON `{"bundle_id",
    "candidates": [{"candidate_id", "shot_id", "width", "frame_quality"}]}`
    naming frames outside the bundle (the picked surplus, labeled after the
    pick; ADR-0008 amendment 2026-09-28). Their composition geometry rides
    in `frame_quality`, copied from the run's breadth-omitted audit."""
    manifest = _load(candidates if candidates else bundle / "manifest.json")
    allowlist = load_allowlist()
    tags_by_candidate = _by_candidate(tags)
    faces_by_candidate = _by_candidate(faces)
    figures_by_candidate = _by_candidate(figures)
    # Composition geometry is read from the run's own audit file, where the
    # extraction stage writes it.
    quality_by_candidate = {}
    if coverage_audit and coverage_audit.is_file():
        quality_by_candidate = _load(coverage_audit).get(
            "published_frame_quality", {})
    proposals = []
    for candidate in manifest["candidates"]:
        candidate_id = candidate["candidate_id"]
        descriptor = _descriptor_for(results, candidate_id)
        tag_entry = tags_by_candidate.get(candidate_id)
        wd = (from_predictions(tag_entry["general_tags"], allowlist,
                               tag_threshold,
                               scene_threshold=scene_tag_threshold)
              if tag_entry else None)
        face_entry = faces_by_candidate.get(candidate_id)
        face_count = face_entry["face_count"] if face_entry else None
        face_fraction = (face_entry.get("largest_face_fraction")
                         if face_entry else None)
        # The `scenery` tag is read raw: the allowlist does not map it, because
        # it names subject matter rather than camera distance. shot_scale uses
        # it only for frames no other lane reaches. It still has
        # to clear the same tagger cut as everything else -- a plain membership
        # test would silently fire on sub-cut tags whenever the manifest retained
        # them, coupling the lane to a manifest's retention floor rather than the
        # configured threshold.
        scenery = bool(
            tag_entry
            and tag_entry.get("general_tags", {}).get("scenery", 0.0) >= tag_threshold
        )
        # Composition needs geometry from the worker's audit plus the face
        # boxes. Both are optional: a bundle produced before image `.11` has no
        # geometry, and composition simply abstains for it rather than failing.
        geometry = (quality_by_candidate.get(candidate_id)
                    or candidate.get("frame_quality") or {})
        # Head boxes (optional, like faces): shot scale and composition use
        # the largest one only where no face answered.
        heads = tuple((figures_by_candidate.get(candidate_id) or {}).get("heads", ()))
        evidence = CandidateEvidence(
            candidate_id=candidate_id,
            shot_id=candidate["shot_id"],
            palette=from_descriptor(descriptor),
            wd=wd,
            face_count=face_count,
            face_fraction=face_fraction,
            scenery_tagged=scenery,
            symmetry=geometry.get("symmetry"),
            is_symmetrical=bool(geometry.get("symmetrical")),
            entropy=geometry.get("entropy"),
            faces=tuple(face_entry.get("faces", ())) if face_entry else (),
            frame_width=int(candidate.get("width") or 0),
            heads=heads,
            head_height=heads[0].get("height_fraction") if heads else None,
        )
        try:
            proposal = build_proposal(
                evidence, provider_id, configuration_ref, run_ref
            )
        except (KeyError, ValueError) as error:
            # One bad candidate used to surface as a bare `'chroma'` against a
            # 200-frame run, with no way to tell which frame or which input.
            raise ValueError(
                f"{candidate_id}: {type(error).__name__}: {error}"
            ) from error
        # Which lane answered shot_scale, recorded beside the proposal rather
        # than inside it: the record schema seals the proposal object, and this
        # is provenance for calibration, not a label. A reviewed sample has to be
        # able to score each lane on its own; a single accuracy figure over a
        # cascade tells you nothing about which signal to fix.
        proposals.append({
            "candidate_id": evidence.candidate_id,
            "shot_id": evidence.shot_id,
            "provenance": {"shot_scale_lane": shot_scale_lane(evidence),
                           "fields": field_sources(evidence)},
            "proposal": proposal,
        })
    return {
        "schema_version": "1.0",
        "protocol_version": taxonomy.PROTOCOL_VERSION,
        "taxonomy_version": taxonomy.TAXONOMY_VERSION,
        # Which tag-to-label mapping produced these labels: a new allowlist
        # changes labels with no code or model change. A note for whoever
        # reads the run later; nothing refuses a run on it.
        "allowlist_version": allowlist.version,
        "bundle_id": manifest["bundle_id"],
        "run_ref": run_ref,
        "configuration": {
            "tag_threshold": tag_threshold,
            "scene_tag_threshold": (scene_tag_threshold
                                    if scene_tag_threshold is not None
                                    else tag_threshold),
            # The head lane's height cuts, so a run says which made its
            # head-lane labels (informational; nothing refuses on it).
            "head_cuts": [cut for cut, _ in HEAD_CUTS],
        },
        "coverage": coverage([p["proposal"] for p in proposals]),
        "content_coverage": _content_coverage(proposals),
        "shot_scale_lanes": _lane_counts(proposals),
        "proposals": proposals,
    }


def _content_coverage(proposals: list[dict]) -> dict:
    """Share of candidates carrying at least one content-facet tag."""
    total = len(proposals) or 1
    counts = {"things": 0, "actions": 0}
    for entry in proposals:
        content = entry["proposal"]["labels"]["content"]
        for facet in counts:
            if content[facet]:
                counts[facet] += 1
    return {facet: round(count / total, 4) for facet, count in counts.items()}


def _lane_counts(proposals: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for entry in proposals:
        lane = entry["provenance"]["shot_scale_lane"]
        counts[lane] = counts.get(lane, 0) + 1
    return dict(sorted(counts.items()))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="annotation-proposals",
        description="Emit bulk taxonomy proposals for a prepared candidate bundle",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle", help="prepared bundle directory")
    source.add_argument("--candidates",
                        help="JSON list of loose candidates in place of a "
                             "bundle (see emit)")
    parser.add_argument("--results", required=True,
                        help="completed colour-result directory for those "
                             "candidates (artifacts/<id>.descriptor.json)")
    parser.add_argument("--tags",
                        help="WD tagger result-manifest.json; its families "
                             "abstain when omitted")
    parser.add_argument("--faces",
                        help="face-detector result-manifest.json; people fuses "
                             "face boxes with tagger counts when given")
    parser.add_argument("--figures",
                        help="head and figure detector result-manifest.json; "
                             "shot scale and composition use the largest head "
                             "where no face answered")
    parser.add_argument("--shot-coverage",
                        help="the run's audit/shot-coverage.json; supplies the "
                             "composition geometry measured at extraction. "
                             "Composition abstains without it")
    parser.add_argument("--tag-threshold", type=float, default=0.35,
                        help="base tagger score cut fed to fusion; provisional "
                             "0.35 by default. Applies to every field except "
                             "the scene fields, and to the content facets.")
    parser.add_argument("--scene-tag-threshold", type=float,
                        default=DEFAULT_SCENE_THRESHOLD,
                        help="cut for setting/time/weather only; 0.2653 (the "
                             "model card's P=R point) was human-validated on "
                             "the reviewed sample. Inert below the "
                             "tags manifest's retention floor -- sub-floor "
                             "scores were never stored.")
    parser.add_argument("--out", required=True, help="proposals JSON to write")
    parser.add_argument("--provider-id", default="provider-fused")
    parser.add_argument("--configuration-ref", default="config-default")
    parser.add_argument("--run-ref", required=True)
    args = parser.parse_args(argv)

    document = emit(
        Path(args.bundle) if args.bundle else None, Path(args.results),
        args.provider_id, args.configuration_ref, args.run_ref,
        tags=Path(args.tags) if args.tags else None,
        faces=Path(args.faces) if args.faces else None,
        coverage_audit=Path(args.shot_coverage) if args.shot_coverage else None,
        tag_threshold=args.tag_threshold,
        scene_tag_threshold=args.scene_tag_threshold,
        candidates=Path(args.candidates) if args.candidates else None,
        figures=Path(args.figures) if args.figures else None,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(
        (json.dumps(document, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
    )
    summary = {
        "proposals": len(document["proposals"]),
        "families_backed": document["coverage"]["families_backed"],
        "families_abstaining": document["coverage"]["families_abstaining"],
        "content_coverage": document["content_coverage"],
        "shot_scale_lanes": document["shot_scale_lanes"],
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"annotation-proposals: {error}", file=sys.stderr)
        sys.exit(1)
