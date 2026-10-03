"""Provenance tracking for the frame sampler."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class SceneScoreProvenance:
    detector: str = "pyscenedetect-adaptive"
    detector_version: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    content_metric: str = "delta_hsv_histogram"
    #: Which video reader decoded the source for scoring. Worth recording
    #: because it is not always the default one: a codec OpenCV cannot read
    #: falls back to PyAV, and a run scored through a different decoder is
    #: worth being able to identify afterwards.
    decode_backend: str = "opencv"

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "detector_version": self.detector_version,
            "parameters": self.parameters,
            "content_metric": self.content_metric,
            "decode_backend": self.decode_backend,
        }

    @classmethod
    def from_scene_score_data(cls, data) -> SceneScoreProvenance:
        return cls(
            detector=data.detector,
            detector_version=data.detector_version,
            parameters=data.parameters,
            content_metric=data.content_metric,
            decode_backend=getattr(data, "decode_backend", "opencv"),
        )
