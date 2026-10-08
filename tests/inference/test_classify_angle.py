"""The camera-angle classifier's input and names (no torch or weights required)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation import taxonomy
from inference.classify_angle import INPUT_SIDE, MEAN, STD, TAXONOMY_NAME, prepare


def test_the_whole_frame_is_squeezed_not_cropped():
    # A wide frame whose left and right thirds differ: a center crop would
    # lose both edges, the squeeze keeps them.
    frame = np.zeros((90, 320, 3), dtype=np.uint8)
    frame[:, :80] = 255
    x = prepare(Image.fromarray(frame))
    assert x.shape == (1, 3, INPUT_SIDE, INPUT_SIDE) and x.dtype == np.float32
    white = (1.0 - MEAN[0]) / STD[0]
    black = (0.0 - MEAN[0]) / STD[0]
    assert abs(x[0, 0, 112, 10] - white) < 1e-5
    assert abs(x[0, 0, 112, 200] - black) < 1e-5


def test_every_model_label_is_a_taxonomy_angle():
    assert set(TAXONOMY_NAME) == {"low", "neutral", "high", "overhead", "dutch"}
    for value in TAXONOMY_NAME.values():
        assert value in taxonomy.ANGLE
