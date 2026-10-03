"""Exclusion interval handling for OP/ED and other timeline regions."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class ExclusionInterval:
    interval_id: str
    start_seconds: float
    end_seconds: float
    reason: str
    source: str
    confidence: str
    run: str
    status: str

    def __post_init__(self) -> None:
        valid_reasons = {
            "opening_theme", "ending_theme", "credit_overlay",
            "content_warning", "preview", "other",
        }
        valid_sources = {
            "human_confirmed", "chapter_marker", "cross_episode_repeated",
            "aniskip_hint", "credit_heuristic", "low_confidence_classifier",
        }
        valid_confidence = {"high", "medium", "low"}
        valid_status = {"exclusion_proposed", "exclusion_approved"}
        if self.reason not in valid_reasons:
            raise ValueError(f"invalid reason: {self.reason}")
        if self.source not in valid_sources:
            raise ValueError(f"invalid source: {self.source}")
        if self.confidence not in valid_confidence:
            raise ValueError(f"invalid confidence: {self.confidence}")
        if self.status not in valid_status:
            raise ValueError(f"invalid status: {self.status}")
        if self.end_seconds < self.start_seconds:
            raise ValueError(
                f"end_seconds ({self.end_seconds}) < start_seconds ({self.start_seconds})"
            )

    @property
    def is_approved(self) -> bool:
        return self.status == "exclusion_approved"

    @property
    def is_proposed(self) -> bool:
        return self.status == "exclusion_proposed"

    def to_dict(self) -> dict:
        return {
            "interval_id": self.interval_id,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "reason": self.reason,
            "source": self.source,
            "confidence": self.confidence,
            "run": self.run,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ExclusionInterval:
        return cls(**{k: d[k] for k in cls.__dataclass_fields__})


def merge_safety_margins(
    intervals: List[ExclusionInterval],
    margin_seconds: float = 2.0,
) -> List[Tuple[float, float, List[str]]]:
    approved = [iv for iv in intervals if iv.is_approved]
    expanded: List[Tuple[float, float, List[str]]] = []
    for iv in approved:
        s = max(0.0, iv.start_seconds - margin_seconds)
        e = iv.end_seconds + margin_seconds
        expanded.append((s, e, [iv.interval_id]))
    expanded.sort(key=lambda x: (x[0], x[1]))
    merged: List[Tuple[float, float, List[str]]] = []
    for s, e, ids in expanded:
        if merged and s <= merged[-1][1]:
            prev_s, prev_e, prev_ids = merged[-1]
            merged[-1] = (prev_s, max(prev_e, e), prev_ids + ids)
        else:
            merged.append((s, e, ids))
    return merged


def apply_exclusions(
    timestamps: List[float],
    intervals: List[ExclusionInterval],
    safety_margin_seconds: float = 2.0,
) -> Tuple[List[float], List[Tuple[float, str]]]:
    merged = merge_safety_margins(intervals, safety_margin_seconds)
    proposed = [iv for iv in intervals if iv.is_proposed]
    kept: List[float] = []
    held: List[Tuple[float, str]] = []
    for ts in timestamps:
        in_approved = any(s <= ts <= e for s, e, _ in merged)
        if in_approved:
            continue
        in_proposed = None
        for iv in proposed:
            if iv.start_seconds <= ts <= iv.end_seconds:
                in_proposed = iv.interval_id
                break
        if in_proposed is not None:
            held.append((ts, in_proposed))
        else:
            kept.append(ts)
    return kept, held


def intervals_from_dicts(dicts: List[dict]) -> List[ExclusionInterval]:
    return [ExclusionInterval.from_dict(d) for d in dicts]
