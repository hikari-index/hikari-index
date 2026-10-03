"""Synthetic unit checks for EXTRACTION_POLICY v3 colour routing."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from frame_sample.pipeline_cli import (
    SRGB_PARAMS_SUFFIX,
    SourceColorInfo,
    build_color_filter,
    color_rule,
)


SCALE_FLAGS = "accurate_rnd+full_chroma_int+full_chroma_inp"
SRGB_SUFFIX = ",setparams=color_primaries=bt709:color_trc=iec61966-2-1"


def _color(
    *,
    color_range=None,
    color_space=None,
    color_transfer=None,
    color_primaries=None,
    coded_height=1080,
):
    return SourceColorInfo(
        color_range=color_range,
        color_space=color_space,
        color_transfer=color_transfer,
        color_primaries=color_primaries,
        coded_height=coded_height,
    )


def _scale(matrix: str, in_range: str = "tv") -> str:
    return (
        f"scale=in_range={in_range}:out_range=full:in_color_matrix={matrix}:"
        f"flags={SCALE_FLAGS},format=rgb24{SRGB_SUFFIX}"
    )


WIDE_LIMITED = (
    "zscale=rangein=limited:matrixin=2020_ncl:transferin=2020_10:"
    "primariesin=2020:range=full:matrix=709:transfer=709:primaries=709,"
    "format=gbrp16le,scale=flags=accurate_rnd,format=rgb24"
    f"{SRGB_SUFFIX}"
)
WIDE_FULL = WIDE_LIMITED.replace("rangein=limited", "rangein=full")
HDR = (
    "tonemapx=tonemap=bt2390:t=bt709:m=bt709:p=bt709,"
    "scale=in_range=tv:out_range=full:in_color_matrix=bt709:"
    f"flags={SCALE_FLAGS},format=rgb24{SRGB_SUFFIX}"
)


@pytest.mark.parametrize(
    ("source", "expected_rule", "expected_filter"),
    [
        (
            _color(
                color_range="tv",
                color_space="bt709",
                color_transfer="bt709",
                color_primaries="bt709",
            ),
            "sdr-bt709",
            _scale("bt709"),
        ),
        (
            _color(
                color_range="pc",
                color_space="bt709",
                color_transfer=None,
                color_primaries=None,
            ),
            "sdr-bt709",
            _scale("bt709", "pc"),
        ),
        (
            _color(
                color_space="bt709",
                color_transfer="bt709",
                color_primaries="bt709",
                coded_height=576,
            ),
            "sdr-bt709",
            _scale("bt709"),
        ),
        (
            _color(
                color_space="smpte170m",
                color_transfer="smpte170m",
                color_primaries="smpte170m",
                coded_height=480,
            ),
            "sdr-bt601",
            _scale("bt601"),
        ),
        (
            _color(color_range="pc", color_space="bt470bg", coded_height=576),
            "sdr-bt601",
            _scale("bt601", "pc"),
        ),
        (
            _color(
                color_space="bt2020nc",
                color_transfer="bt2020-10",
                color_primaries="bt2020",
                coded_height=2160,
            ),
            "sdr-bt2020",
            WIDE_LIMITED,
        ),
        (
            _color(
                color_range="pc",
                color_space="bt2020nc",
                color_transfer="bt2020-12",
                color_primaries="bt2020",
                coded_height=2160,
            ),
            "sdr-bt2020",
            WIDE_FULL,
        ),
        (
            _color(
                color_range="tv",
                color_space="bt2020nc",
                color_transfer="smpte2084",
                color_primaries="bt2020",
                coded_height=2160,
            ),
            "hdr-bt2390",
            HDR,
        ),
        (
            _color(
                color_range="pc",
                color_space="bt2020nc",
                color_transfer="arib-std-b67",
                color_primaries="bt2020",
                coded_height=2160,
            ),
            "hdr-bt2390",
            HDR.replace("in_range=tv", "in_range=pc"),
        ),
        (_color(coded_height=720), "sdr-bt709", _scale("bt709")),
        (_color(coded_height=719), "sdr-bt601", _scale("bt601")),
        (
            _color(color_range="pc", coded_height=720),
            "sdr-bt709",
            _scale("bt709", "pc"),
        ),
        (
            _color(color_range="pc", coded_height=719),
            "sdr-bt601",
            _scale("bt601", "pc"),
        ),
    ],
)
def test_color_rules_build_the_exact_frozen_filter(
    source, expected_rule, expected_filter
):
    assert color_rule(source) == expected_rule
    assert build_color_filter(source) == expected_filter


@pytest.mark.parametrize(
    "source",
    [
        _color(color_space="fcc"),
        _color(color_space="ycgco"),
        _color(color_space="bt2020nc"),
        _color(
            color_space="bt2020nc",
            color_transfer="bt2020-10",
            color_primaries="bt709",
        ),
        _color(color_space="bt709", color_transfer="bt2020-10"),
        _color(
            color_space="smpte170m",
            color_transfer="bt2020-10",
            color_primaries="smpte170m",
            coded_height=480,
        ),
        _color(
            color_space="bt470bg",
            color_transfer="gamma28",
            color_primaries="bt470bg",
            coded_height=576,
        ),
        _color(color_range="jpeg", color_space="bt709"),
    ],
)
def test_unknown_or_contradictory_combinations_are_refused(source):
    with pytest.raises(
        ValueError,
        match=(
            r"refusing to guess: color_range=.*color_space=.*"
            r"color_transfer=.*color_primaries=.*coded_height="
        ),
    ):
        build_color_filter(source)


@pytest.mark.parametrize(
    "known_tag",
    [
        {"color_space": "bt709"},
        {"color_transfer": "bt709"},
        {"color_primaries": "bt709"},
    ],
)
def test_each_single_known_709_tag_routes_hd_to_bt709(known_tag):
    source = _color(color_range="tv", coded_height=1080, **known_tag)
    assert color_rule(source) == "sdr-bt709"
    assert build_color_filter(source) == _scale("bt709")


@pytest.mark.parametrize(
    "known_tag",
    [
        {"color_space": "bt709"},
        {"color_transfer": "bt709"},
        {"color_primaries": "bt709"},
    ],
)
def test_each_single_known_709_tag_is_refused_below_hd(known_tag):
    source = _color(color_range="tv", coded_height=576, **known_tag)
    with pytest.raises(ValueError, match="refusing to guess"):
        build_color_filter(source)


def test_unknown_probe_tokens_count_as_absent():
    source = _color(
        color_range="unknown",
        color_space="unspecified",
        color_transfer="N/A",
        color_primaries="unknown",
        coded_height=720,
    )
    assert color_rule(source) == "sdr-bt709"
    assert build_color_filter(source) == _scale("bt709")


def test_source_color_info_reads_all_four_probed_tags_and_coded_height():
    source = SourceColorInfo.from_probe(
        {
            "height": 1080,
            "declared_color": {
                "range": "pc",
                "space": "bt709",
                "transfer": "bt709",
                "primaries": "bt709",
            },
        }
    )
    assert source == SourceColorInfo("pc", "bt709", "bt709", "bt709", 1080)


@pytest.mark.parametrize(
    "source",
    [
        _color(color_space="bt709"),
        _color(color_space="smpte170m", coded_height=480),
        _color(
            color_space="bt2020nc",
            color_transfer="bt2020-10",
            color_primaries="bt2020",
        ),
        _color(color_space="bt2020nc", color_transfer="smpte2084"),
        _color(coded_height=720),
        _color(coded_height=719),
    ],
)
def test_every_filter_ends_with_the_canonical_srgb_declaration(source):
    assert SRGB_PARAMS_SUFFIX == SRGB_SUFFIX
    assert build_color_filter(source).endswith(SRGB_SUFFIX)
