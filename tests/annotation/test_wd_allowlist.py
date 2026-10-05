"""Checks for the WD tag cull and its mapping onto the taxonomy."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation import taxonomy
from annotation.wd_labels import ALLOWLIST_PATH, cull_report, from_predictions, load

ALLOWLIST = load()

# Real tags from the checkpoint that must never reach a record.
FORBIDDEN = [
    "dark_skin", "dark-skinned_female", "dark-skinned_male",  # demographic
    "breasts", "large_breasts",                               # body/sexualised
    "long_hair", "blue_eyes", "blonde_hair", "blush", "smile",  # appearance
    "watermark", "signature", "logo", "copyright_name",       # artifacts
    "hatsune_miku",                                           # named character
]

# Tags a substring search would wrongly capture; the reason matching is exact.
SUBSTRING_TRAPS = ["bloomers", "eyeshadow", "dark_persona", "dark_blue_hair",
                   "glowing_eyes", "shaded_face"]


class TestCull:
    @pytest.mark.parametrize("tag", FORBIDDEN)
    def test_forbidden_tags_are_not_mapped(self, tag):
        assert ALLOWLIST.lookup(tag) is None

    @pytest.mark.parametrize("tag", SUBSTRING_TRAPS)
    def test_substring_traps_are_not_mapped(self, tag):
        """'bloom' catches bloomers, 'shadow' catches eyeshadow, 'dark'
        catches dark_skin. Exact matching is what keeps these out."""
        assert ALLOWLIST.lookup(tag) is None

    def test_lookup_is_exact_not_prefix(self):
        assert ALLOWLIST.lookup("indoors") is not None
        assert ALLOWLIST.lookup("indoor") is None
        assert ALLOWLIST.lookup("indoorsy") is None

    def test_default_is_discard(self):
        report = cull_report(["1girl", "long_hair", "invented_tag", "night"], ALLOWLIST)
        assert report["tags_mapped"] == 2
        assert report["tags_discarded"] == 2
        assert report["mapped_tags"] == ["1girl", "night"]

    def test_allowlist_is_a_small_fraction_of_the_vocabulary(self):
        document = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
        total = document["source_vocabulary"]["tags_total"]
        assert len(ALLOWLIST.mappings) < total * 0.01

    def test_every_mapping_targets_a_real_taxonomy_value(self):
        for mapping in ALLOWLIST.mappings.values():
            taxonomy.check(mapping.field, mapping.value)


class TestGenderIsDropped:
    def test_gendered_count_tags_yield_the_same_arity(self):
        girl = from_predictions({"1girl": 0.9}, ALLOWLIST)
        boy = from_predictions({"1boy": 0.9}, ALLOWLIST)
        assert girl.value("people") == boy.value("people") == "one"

    def test_two_of_either_is_two(self):
        assert from_predictions({"2girls": 0.9}, ALLOWLIST).value("people") == "two"
        assert from_predictions({"2boys": 0.9}, ALLOWLIST).value("people") == "two"

    def test_no_mapping_carries_a_gender_field(self):
        for mapping in ALLOWLIST.mappings.values():
            assert mapping.field != "gender"
        assert "gender" not in taxonomy.VALUES


class TestFieldResolution:
    def test_primary_tag_sets_the_value(self):
        labels = from_predictions({"night": 0.88}, ALLOWLIST)
        assert labels.value("time") == "night"
        assert labels.score("time") > 0.5

    def test_corroborating_tag_cannot_win_alone(self):
        """`portrait` implies a close-up but never decides it by itself."""
        assert from_predictions({"portrait": 0.9}, ALLOWLIST).value("shot_scale") == "abstain"

    def test_corroboration_lifts_a_primary_value(self):
        alone = from_predictions({"close-up": 0.6}, ALLOWLIST).score("shot_scale")
        backed = from_predictions({"close-up": 0.6, "portrait": 0.6}, ALLOWLIST)
        assert backed.value("shot_scale") == "close-up"
        assert backed.score("shot_scale") > alone

    def test_disagreement_damps_the_score(self):
        """A split decision must read as uncertain so it routes to review."""
        clean = from_predictions({"indoors": 0.9}, ALLOWLIST)
        split = from_predictions({"indoors": 0.9, "outdoors": 0.85}, ALLOWLIST)
        assert split.score("setting") < clean.score("setting")

    def test_below_threshold_predictions_are_ignored(self):
        assert from_predictions({"night": 0.1}, ALLOWLIST).value("time") == "abstain"

    def test_unmapped_families_abstain(self):
        labels = from_predictions({"1girl": 0.9}, ALLOWLIST)
        assert labels.value("weather") == "abstain"
        assert labels.score("weather") == 0.0

    def test_lighting_values_palette_cannot_reach(self):
        """backlit and silhouette need spatial evidence the colour descriptor
        structurally does not carry."""
        assert from_predictions({"backlighting": 0.8}, ALLOWLIST).value("lighting") == "backlit"
        assert from_predictions({"silhouette": 0.8}, ALLOWLIST).value("lighting") == "silhouette"


class TestTextEvidence:
    def test_text_tags_flag_presence_without_asserting_a_quality_value(self):
        labels = from_predictions({"subtitled": 0.7}, ALLOWLIST)
        assert labels.text_present is True
        # Presence is a review trigger, not a classification.
        assert "quality" not in labels.fields

    def test_no_text_tags_means_no_flag(self):
        assert from_predictions({"night": 0.9}, ALLOWLIST).text_present is False


def test_every_corroborating_value_has_a_primary_tag():
    """A corroborating tag only adds to a value some primary tag names
    (`_resolve_field`), so one whose value no primary tag shares can never
    affect a label. `scenery` pointed at `extreme-wide`, which no primary tag
    names, and did nothing."""
    primary = {(m.field, m.value) for m in ALLOWLIST.mappings.values()
               if m.role == "primary"}
    dead = sorted(m.tag for m in ALLOWLIST.mappings.values()
                  if m.role == "corroborating" and (m.field, m.value) not in primary)
    assert dead == []


def test_scenery_corroborates_the_value_its_lane_answers():
    """The scenery lane (shot_scale.from_scenery) answers `wide`; the
    allowlist must not say the same tag means something else."""
    assert ALLOWLIST.lookup("scenery").value == "wide"
    wide = from_predictions({"wide_shot": 0.5, "scenery": 0.5}, ALLOWLIST)
    alone = from_predictions({"wide_shot": 0.5}, ALLOWLIST)
    assert wide.value("shot_scale") == "wide"
    assert wide.score("shot_scale") > alone.score("shot_scale")


def test_allowlist_version_pins_the_taxonomy():
    document = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    assert document["taxonomy_version"] == taxonomy.TAXONOMY_VERSION
    assert document["source_vocabulary"]["copyright_or_series"] == 0


class TestContentFacets:
    """Multi-value open-vocabulary facets: things and actions (taxonomy v3)."""

    def test_facet_tags_reach_the_record_verbatim(self):
        labels = from_predictions({"sky": 0.9, "tree": 0.6, "holding": 0.8},
                                  ALLOWLIST)
        assert labels.things == (("sky", 0.9), ("tree", 0.6))
        assert labels.actions == (("holding", 0.8),)

    def test_non_facet_tags_do_not_leak_in(self):
        """Expression and appearance vocabulary stays deliberately excluded."""
        labels = from_predictions({"smile": 0.95, "blush": 0.9, "long_hair": 0.9},
                                  ALLOWLIST)
        assert labels.things == ()
        assert labels.actions == ()

    def test_facets_use_the_base_cut_not_the_scene_cut(self):
        labels = from_predictions({"sky": 0.30}, ALLOWLIST,
                                  threshold=0.35, scene_threshold=0.2653)
        assert labels.things == ()

    def test_no_forbidden_vocabulary_in_the_facet_lists(self):
        """Age, demographic, sexualised, and franchise terms never enter."""
        forbidden = {"child", "old", "old_man", "old_woman", "breasts",
                     "dark_skin", "loli", "shota", "kita_high_school_uniform",
                     "plugsuit", "serafuku", "school_uniform"}
        assert not (ALLOWLIST.things | ALLOWLIST.actions) & forbidden

    def test_a_facet_tag_that_is_also_mapped_serves_both(self):
        """`rain` is a weather primary AND a thing; weather left the display
        set, so the facet is what keeps rain frames searchable."""
        labels = from_predictions({"rain": 0.8}, ALLOWLIST)
        assert labels.value("weather") == "rain"
        assert ("rain", 0.8) in labels.things


class TestSceneThreshold:
    """setting/time/weather cut human-validated at 0.2653 (2026-07-26)."""

    def test_scene_fields_use_the_scene_cut(self):
        labels = from_predictions({"night": 0.30}, ALLOWLIST,
                                  threshold=0.35, scene_threshold=0.2653)
        assert labels.value("time") == "night"

    def test_non_scene_fields_keep_the_base_cut(self):
        """A lower tagger cut on shot_scale overrides the validated
        face-occupancy lane: 40 of 41 flips in the 0.2653 re-fusion."""
        labels = from_predictions({"close-up": 0.30}, ALLOWLIST,
                                  threshold=0.35, scene_threshold=0.2653)
        assert labels.value("shot_scale") == "abstain"

    def test_default_is_one_cut_for_everything(self):
        labels = from_predictions({"night": 0.30}, ALLOWLIST)
        assert labels.value("time") == "abstain"

    def test_derived_mixed_setting_uses_the_scene_cut(self):
        labels = from_predictions({"indoors": 0.30, "outdoors": 0.28},
                                  ALLOWLIST, scene_threshold=0.2653)
        assert labels.value("setting") == "mixed"


def test_display_set_and_record_fields_partition_cleanly():
    """Every single-value field is either displayed or deliberately not."""
    single_valued = set(taxonomy.VALUES) - {"palette_evidence"}
    assert set(taxonomy.DISPLAY_FIELDS) | set(taxonomy.NON_DISPLAY_FIELDS) == single_valued
    assert not set(taxonomy.DISPLAY_FIELDS) & set(taxonomy.NON_DISPLAY_FIELDS)


class TestDerivedValues:
    """Taxonomy values no tag names, but the evidence implies."""

    def test_both_sides_firing_is_mixed_setting(self):
        labels = from_predictions({"indoors": 0.8, "outdoors": 0.7}, ALLOWLIST)
        assert labels.value("setting") == "mixed"

    def test_one_side_is_not_mixed(self):
        assert from_predictions({"indoors": 0.9}, ALLOWLIST).value("setting") == "interior"
        assert from_predictions({"outdoors": 0.9}, ALLOWLIST).value("setting") == "exterior"

    def test_interior_implies_weather_not_visible(self):
        labels = from_predictions({"indoors": 0.9}, ALLOWLIST)
        assert labels.value("weather") == "none-visible"

    def test_visible_weather_survives_an_interior(self):
        """Rain through a window is real evidence and must not be overwritten."""
        labels = from_predictions({"indoors": 0.9, "rain": 0.7}, ALLOWLIST)
        assert labels.value("weather") == "rain"

    def test_exterior_does_not_imply_weather(self):
        assert from_predictions({"outdoors": 0.9}, ALLOWLIST).value("weather") == "abstain"

    def test_mixed_setting_does_not_imply_weather(self):
        labels = from_predictions({"indoors": 0.8, "outdoors": 0.7}, ALLOWLIST)
        assert labels.value("weather") == "abstain"


class TestPeopleCounting:
    """Count tags are additive across gender, not rival values."""

    def test_mixed_group_sums(self):
        """The first real run undercounted every mixed group: 2girls and 1boy
        firing together is three people, not a contest between two and one."""
        labels = from_predictions({"2girls": 0.9, "1boy": 0.8}, ALLOWLIST)
        assert labels.value("people") == "small-group"

    def test_one_of_each_is_two(self):
        assert from_predictions({"1girl": 0.9, "1boy": 0.85}, ALLOWLIST).value("people") == "two"

    def test_within_one_gender_the_largest_wins(self):
        """1girl and 2girls are mutually exclusive; they must not sum to three."""
        assert from_predictions({"1girl": 0.6, "2girls": 0.9}, ALLOWLIST).value("people") == "two"

    def test_no_humans_is_zero(self):
        assert from_predictions({"no_humans": 0.9}, ALLOWLIST).value("people") == "zero"

    def test_no_humans_beats_a_stray_count_tag(self):
        labels = from_predictions({"no_humans": 0.9, "1girl": 0.4}, ALLOWLIST)
        assert labels.value("people") == "zero"

    def test_solo_settles_the_field(self):
        """`solo` asserts a total outright rather than contributing to a sum."""
        assert from_predictions({"solo": 0.95, "1girl": 0.9}, ALLOWLIST).value("people") == "one"

    def test_crowd_tag_wins_directly(self):
        assert from_predictions({"crowd": 0.7, "2girls": 0.8}, ALLOWLIST).value("people") == "crowd"

    def test_six_plus_is_crowd(self):
        assert from_predictions({"6+girls": 0.8}, ALLOWLIST).value("people") == "crowd"

    def test_mixed_group_is_scored_less_confidently(self):
        """A sum of two independent predictions is less certain than one tag
        carrying the whole count."""
        single = from_predictions({"2girls": 0.9}, ALLOWLIST).score("people")
        mixed = from_predictions({"2girls": 0.9, "1boy": 0.9}, ALLOWLIST).score("people")
        assert mixed < single

    def test_couple_alone_is_a_weak_two(self):
        labels = from_predictions({"couple": 0.9}, ALLOWLIST)
        assert labels.value("people") == "two"
        assert labels.score("people") < 0.9

    def test_gender_still_leaves_no_trace(self):
        girls = from_predictions({"3girls": 0.9}, ALLOWLIST)
        boys = from_predictions({"3boys": 0.9}, ALLOWLIST)
        assert girls.value("people") == boys.value("people") == "small-group"
