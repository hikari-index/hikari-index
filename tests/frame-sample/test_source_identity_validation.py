"""What the pipeline asks of the identity it is handed, before it spends an
hour extracting: the names and ids it needs are there and the entry type is
one the folder naming knows. A field it does not read is carried into the
bundle untouched and is never a reason to refuse.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from frame_sample.pipeline_cli import _validate_source_identity


def _identity(**overrides) -> dict:
    base = {
        "series_title": "A Series",
        "entry_type": "episode",
        "episode": 9,
        "episode_title": "An Episode",
        "source_filename": "a.mkv",
        "series_id": 1,
        "episode_ids": [2],
        "file_id": 3,
        "work_id": "a-series-e09",
    }
    base.update(overrides)
    return base


def test_a_well_formed_identity_passes():
    _validate_source_identity(_identity())


def test_the_optional_fields_are_allowed():
    _validate_source_identity(_identity(season=2))


def test_a_missing_required_field_is_named():
    identity = _identity()
    del identity["work_id"]
    with pytest.raises(ValueError, match="work_id"):
        _validate_source_identity(identity)


def test_fields_the_pipeline_does_not_read_are_carried_not_refused():
    _validate_source_identity(_identity(studio="A Studio", _size_bytes=1))


@pytest.mark.parametrize("entry_type", ["film", "special", "ova", ""])
def test_only_the_two_entry_types_are_accepted(entry_type):
    """`film` and `special` read as reasonable; the folder naming knows
    only `movie` and `episode`."""
    with pytest.raises(ValueError, match="entry_type"):
        _validate_source_identity(_identity(entry_type=entry_type))


@pytest.mark.parametrize("entry_type", ["movie", "episode"])
def test_the_two_entry_types_are_accepted(entry_type):
    _validate_source_identity(_identity(entry_type=entry_type))


def test_an_episode_needs_an_episode_id():
    with pytest.raises(ValueError, match="episode id"):
        _validate_source_identity(_identity(episode_ids=[]))


def test_a_movie_may_have_none():
    _validate_source_identity(_identity(entry_type="movie", episode_ids=[]))


def test_episode_ids_must_be_a_list():
    with pytest.raises(ValueError, match="must be a list"):
        _validate_source_identity(_identity(episode_ids=2))
