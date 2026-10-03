"""Checks for the bulk proposal emitter and its palette mappings."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation import taxonomy
from annotation.palette_labels import from_descriptor
from annotation.proposal import CandidateEvidence, build_proposal, coverage


def descriptor(bands, sat, luma_mean, p05=0.2, p95=0.8):
    return {
        "hue_family": {"bands": bands,
                       "band_centers_deg": [0, 30, 60, 120, 180, 220, 275, 320]},
        "chroma": {"mean_sat_weighted": sat},
        "luma": {"mean": luma_mean, "p05": p05, "p50": luma_mean, "p95": p95},
    }


WARM = [0.3, 0.4, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0]
COOL = [0.0, 0.0, 0.0, 0.0, 0.3, 0.5, 0.2, 0.0]
SPLIT = [0.2, 0.2, 0.1, 0.0, 0.2, 0.2, 0.1, 0.0]


class TestPaletteMapping:
    def test_warm_and_cool_separate(self):
        assert from_descriptor(descriptor(WARM, 0.45, 0.4)).color_bias == "warm"
        assert from_descriptor(descriptor(COOL, 0.45, 0.4)).color_bias == "cool"

    def test_dark_scene_still_reads_its_hue(self):
        """Cb/Cr shrinks with luma; hue mass does not. A dark orange interior
        measured +0.079 warmth on Cb/Cr and would have been called neutral."""
        assert from_descriptor(descriptor(WARM, 0.51, 0.129)).color_bias == "warm"

    def test_both_sides_present_reads_mixed(self):
        assert from_descriptor(descriptor(SPLIT, 0.45, 0.5)).color_bias == "mixed"

    def test_desaturated_reads_monochrome_before_hue(self):
        assert from_descriptor(descriptor(WARM, 0.05, 0.5)).color_bias == "monochrome"

    @pytest.mark.parametrize("sat,expected", [
        (0.16, "muted"), (0.45, "balanced"), (0.80, "vivid"),
    ])
    def test_saturation_bands(self, sat, expected):
        assert from_descriptor(descriptor(WARM, sat, 0.5)).saturation == expected

    def test_low_and_high_key(self):
        assert from_descriptor(descriptor(WARM, 0.5, 0.13, p95=0.26)).lighting == "low-key"
        assert from_descriptor(descriptor(WARM, 0.5, 0.84, p05=0.45)).lighting == "high-key"

    def test_bright_with_crushed_shadows_abstains(self):
        """Real lighting character, but not one luma percentiles can name."""
        assert from_descriptor(descriptor(WARM, 0.6, 0.74, p05=0.16)).lighting == "abstain"

    def test_missing_descriptor_abstains_wholesale(self):
        labels = from_descriptor(None)
        assert labels.palette_evidence == "abstain"
        assert labels.color_bias == labels.saturation == labels.lighting == "abstain"


def _evidence():
    return CandidateEvidence(
        candidate_id="cand-0004", shot_id="shot-001",
        palette=from_descriptor(descriptor(WARM, 0.45, 0.4)),
    )


class TestProposal:
    def test_every_family_is_present(self):
        labels = build_proposal(_evidence(), "provider-palette", "config-default", "run-test")["labels"]
        assert {family.replace("-", "_") for family in taxonomy.FAMILIES} <= set(labels)

    def test_unbacked_families_abstain_rather_than_guess(self):
        labels = build_proposal(_evidence(), "provider-palette", "config-default", "run-test")["labels"]
        assert labels["setting_time_weather"]["setting"] == "abstain"
        assert labels["people"] == "abstain"
        assert labels["shot_scale"] == "abstain"
        assert labels["angle_composition"]["angle"] == "abstain"

    def test_backed_families_carry_real_values(self):
        labels = build_proposal(_evidence(), "provider-palette", "config-default", "run-test")["labels"]
        group = labels["lighting_color_character"]
        assert group["color_bias"] == "warm"
        assert group["saturation"] == "balanced"
        assert group["palette_evidence"] == "available"

    def test_abstaining_families_score_zero(self):
        scores = build_proposal(_evidence(), "provider-palette", "config-default", "run-test")["scores"]
        by_family = {s["family"]: s for s in scores}
        assert by_family["people"]["score"] == 0.0
        assert by_family["setting-time-weather"]["score"] == 0.0
        assert by_family["lighting-color-character"]["score"] > 0.0

    def test_quality_scores_low_enough_to_route_for_review(self):
        """Nothing detects text cards yet, so a confident `usable` would be a
        lie. The low score is what sends the family to a human."""
        proposal = build_proposal(_evidence(), "provider-palette", "config-default", "run-test")
        quality = next(s for s in proposal["scores"] if s["family"] == "quality")
        assert quality["score"] < 0.5
        assert proposal["labels"]["quality"]["selection_disposition"] == "review"

    def test_scores_cover_each_family_exactly_once(self):
        scores = build_proposal(_evidence(), "provider-palette", "config-default", "run-test")["scores"]
        families = [s["family"] for s in scores]
        assert sorted(families) == sorted(taxonomy.FAMILIES)
        assert len(families) == len(set(families))

    def test_rejects_a_non_opaque_provider_id(self):
        with pytest.raises(ValueError):
            build_proposal(_evidence(), "1nvalid", "config-x", "run-x")

    def test_coverage_names_the_gaps(self):
        proposals = [build_proposal(_evidence(), "provider-palette", "config-default", "run-test")]
        report = coverage(proposals)
        assert report["families_backed"] == ["lighting-color-character", "quality"]
        assert "people" in report["families_abstaining"]
        assert "setting-time-weather" in report["families_abstaining"]


class TestTaggerBackedProposal:
    """The proposal once WD predictions are available."""

    def _with_tags(self, predictions):
        from annotation.wd_labels import from_predictions, load
        return CandidateEvidence(
            candidate_id="cand-0004", shot_id="shot-001",
            palette=from_descriptor(descriptor(WARM, 0.45, 0.4)),
            wd=from_predictions(predictions, load()),
        )

    def test_tagger_fills_the_families_palette_cannot(self):
        evidence = self._with_tags(
            {"outdoors": 0.9, "night": 0.85, "rain": 0.7,
             "close-up": 0.8, "from_below": 0.75, "2girls": 0.9}
        )
        labels = build_proposal(evidence, "provider-wdtagger", "config-default", "run-test")["labels"]
        assert labels["setting_time_weather"] == {
            "setting": "exterior", "time": "night", "weather": "rain"
        }
        assert labels["shot_scale"] == "close-up"
        assert labels["angle_composition"]["angle"] == "low"
        assert labels["people"] == "two"

    def test_composition_still_abstains(self):
        """No tagger route exists; it waits on spatial rules."""
        evidence = self._with_tags({"outdoors": 0.9})
        labels = build_proposal(evidence, "provider-wdtagger", "config-default", "run-test")["labels"]
        assert labels["angle_composition"]["composition"] == "abstain"

    def test_tagger_lighting_beats_a_palette_abstention(self):
        evidence = self._with_tags({"backlighting": 0.8})
        assert evidence.palette.lighting == "abstain"
        labels = build_proposal(evidence, "provider-wdtagger", "config-default", "run-test")["labels"]
        assert labels["lighting_color_character"]["lighting"] == "backlit"

    def test_tagger_monochrome_overrides_the_palette_threshold(self):
        """The palette monochrome bound was never triggered by a real frame;
        a direct assertion is stronger evidence."""
        evidence = self._with_tags({"greyscale": 0.9})
        assert evidence.palette.color_bias == "warm"
        labels = build_proposal(evidence, "provider-wdtagger", "config-default", "run-test")["labels"]
        assert labels["lighting_color_character"]["color_bias"] == "monochrome"

    def test_coverage_grows_with_the_tagger(self):
        without = build_proposal(_evidence(), "provider-wdtagger", "config-default", "run-test")
        with_tags = build_proposal(
            self._with_tags({"outdoors": 0.9, "1girl": 0.9, "close-up": 0.8}),
            "provider-wdtagger", "config-default", "run-test",
        )
        assert len(coverage([without])["families_backed"]) == 2
        assert len(coverage([with_tags])["families_backed"]) > 2

    def test_tagger_backed_proposal_uses_only_taxonomy_values(self):
        evidence = self._with_tags(
            {"indoors": 0.9, "day": 0.8, "1boy": 0.85, "upper_body": 0.7,
             "from_above": 0.6, "silhouette": 0.7}
        )
        labels = build_proposal(evidence, "provider-wdtagger", "config-default", "run-test")["labels"]
        assert labels["people"] in taxonomy.PEOPLE
        assert labels["shot_scale"] in taxonomy.SHOT_SCALE
        assert labels["setting_time_weather"]["setting"] in taxonomy.SETTING
        assert labels["lighting_color_character"]["lighting"] in taxonomy.LIGHTING
