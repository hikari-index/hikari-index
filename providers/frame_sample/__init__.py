"""The deterministic frame sampler: the extraction stage of Hikari Index."""

from .sampler import FrameSampler, SamplerResult, SamplerParams
from .exclusion import ExclusionInterval, apply_exclusions, merge_safety_margins
from .dedup import compute_phash_bytes, group_dedup_families
from .provenance import SceneScoreProvenance

__all__ = [
    "FrameSampler",
    "SamplerResult",
    "SamplerParams",
    "ExclusionInterval",
    "apply_exclusions",
    "merge_safety_margins",
    "compute_phash_bytes",
    "group_dedup_families",
    "SceneScoreProvenance",
]
