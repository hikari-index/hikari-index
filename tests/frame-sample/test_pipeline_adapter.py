"""Focused checks for the selector-to-prepared-candidate adapter."""

from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from frame_sample.frame_quality import (
    DEFAULT_CROSS_SHOT_DUPLICATE,
    DEFAULT_INTRA_SHOT_DISTINCT,
    DEFAULT_MIN_DETAIL,
    DEFAULT_MIN_ENTROPY,
)
from frame_sample.pipeline_cli import (
    SourceColorInfo,
    _bundle_digest,
    build_color_filter,
    _partition_candidates,
    _publish_best_in_shot,
    _rank_shots,
)
from frame_sample.sampler import CandidateRecord


def _candidate(
    index: int,
    score: float,
    status: str = "cleared",
    shot: int | None = None,
    timestamp: float | None = None,
) -> CandidateRecord:
    return CandidateRecord(
        candidate_id=f"cand-{index:04d}",
        shot_id=f"shot-{(index if shot is None else shot):03d}",
        timestamp_seconds=float(index if timestamp is None else timestamp),
        scene_score=score,
        exclusion_status=status,
    )


def test_partition_excludes_held_pixels():
    candidates = [
        _candidate(1, 1.0),
        _candidate(2, 9.0),
        _candidate(3, 7.0, "held_unresolved"),
        _candidate(4, 8.0),
    ]

    cleared, held = _partition_candidates(candidates)

    assert [candidate.candidate_id for candidate in cleared] == [
        "cand-0001", "cand-0002", "cand-0004"
    ]
    assert [candidate.candidate_id for candidate in held] == ["cand-0003"]


def test_the_bundle_digest_follows_the_manifest_and_nothing_else():
    assert _bundle_digest("a" * 64) == _bundle_digest("a" * 64)
    assert _bundle_digest("a" * 64) != _bundle_digest("b" * 64)


class TestRankShots:
    def test_samples_group_by_shot_and_sort_by_time(self):
        candidates = [
            _candidate(3, 5.0, shot=1, timestamp=30.0),
            _candidate(1, 5.0, shot=1, timestamp=10.0),
            _candidate(2, 9.0, shot=2, timestamp=20.0),
        ]

        ranked = _rank_shots(candidates)

        assert [shot_id for shot_id, _ in ranked] == ["shot-002", "shot-001"]
        assert [c.timestamp_seconds for c in ranked[1][1]] == [10.0, 30.0]

    def test_equal_scores_break_on_first_timestamp(self):
        candidates = [
            _candidate(1, 7.0, shot=9, timestamp=90.0),
            _candidate(2, 7.0, shot=4, timestamp=40.0),
        ]

        assert [shot_id for shot_id, _ in _rank_shots(candidates)] == [
            "shot-004", "shot-009"
        ]


def _png(pattern) -> bytes:
    """Render a 64x64 RGB PNG from a callable over (x, y)."""
    from PIL import Image
    img = Image.new("RGB", (64, 64))
    img.putdata([pattern(x, y) for y in range(64) for x in range(64)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _textured(seed: int):
    """A detailed frame; `seed` shifts the pattern enough to change the picture."""
    return lambda x, y: (
        (x * 7 + y * 3 + seed * 41) % 256,
        (x * 3 + y * 11 + seed * 97) % 256,
        (x * 13 + y * 5 + seed * 17) % 256,
    )


def _twin_of(seed: int, nudge: int):
    """Same framing as `_textured(seed)`, perturbed like animation micro-motion."""
    base = _textured(seed)
    return lambda x, y: base(x, y) if (x + y) % 16 else (
        (base(x, y)[0] + nudge) % 256, base(x, y)[1], base(x, y)[2]
    )


def _flat(level: int):
    """A fade or blank card: no edge energy, no entropy."""
    return lambda x, y: (level, level, level)


def _silhouette(field: int, subject: int, size: int = 14):
    """A lit subject in a void: luma-extreme and featureless, but not blank.

    This is the shape a review of real runs found the old floor destroying -- petals
    on black, a figure against white sky, a lit doorway at the end of a tunnel.
    It measures below both featurelessness floors, so the floor questions it,
    but its luma is emphatically not one value: the synthetic case here lands at
    stddev 0.208 against the 0.005 blank cut.
    """
    edge = (64 - size) // 2
    return lambda x, y: (
        (subject,) * 3 if edge <= x < edge + size and edge <= y < edge + size
        else (field,) * 3
    )


def _run_publish(candidates, frames, last_frame=None, **overrides):
    """Drive _publish_best_in_shot with frames keyed by candidate_id.

    `last_frame` (seconds) ends the picture there: timestamps after it have no
    frame, as when a file's audio outlasts its video or its bytes are damaged.
    """
    color_filter = build_color_filter(SourceColorInfo(None, None, None, None, 720))
    ends = (lambda t: False) if last_frame is None else (lambda t: t > last_frame)
    settings = {
        "max_candidates": 8,
        "max_extractions": 64,
        "min_detail": DEFAULT_MIN_DETAIL,
        "min_entropy": DEFAULT_MIN_ENTROPY,
        "intra_shot_distinct": DEFAULT_INTRA_SHOT_DISTINCT,
        "cross_shot_duplicate": DEFAULT_CROSS_SHOT_DUPLICATE,
        # Short source, so the planned frames outweigh one decode and the
        # batched path is chosen. Pass a large source_duration to exercise
        # per-frame seeking instead.
        "source_duration": 1.0,
    }
    settings.update(overrides)

    def fake_resolve_pts(input_path, timestamp, time_base, ffprobe_path):
        return None if ends(timestamp) else int(timestamp * 1000)

    def fake_resolve_bulk(input_path, timestamps, time_base, ffprobe_path):
        return [None if ends(t) else int(t * 1000) for t in timestamps]

    def fake_batch(input_path, wanted, stage, ffmpeg_path,
                   applied_filter=color_filter, time_base="1/1000",
                   seek_preroll=1.0):
        """Stand in for one decode pass: write every requested frame."""
        assert applied_filter == color_filter
        order = [pts for _, pts in wanted]
        assert order == sorted(order), (
            "batch extraction must receive candidates in pts order"
        )
        for candidate_id, pts in wanted:
            (stage / f"{candidate_id}.png").write_bytes(frames[candidate_id])
        return dict(wanted)

    def fake_single(input_path, timestamp, intended_pts, output_path, ffmpeg_path,
                    applied_filter=color_filter, seek_preroll=1.0):
        """Stand in for the per-frame path."""
        assert applied_filter == color_filter
        output_path.write_bytes(frames[output_path.stem])
        return intended_pts

    with patch("frame_sample.pipeline_cli._resolve_pts", side_effect=fake_resolve_pts), \
         patch("frame_sample.pipeline_cli._resolve_pts_bulk", side_effect=fake_resolve_bulk), \
         patch("frame_sample.pipeline_cli._extract_batch", side_effect=fake_batch), \
         patch("frame_sample.pipeline_cli._extract_exact_frame", side_effect=fake_single), \
         patch("frame_sample.pipeline_cli._container_start_time", return_value=0.0), \
         patch("frame_sample.pipeline_cli._image_dimensions", return_value=(64, 64)), \
         patch("frame_sample.pipeline_cli._last_frame_seconds", return_value=last_frame), \
         patch("frame_sample.pipeline_cli._sha256", return_value="a" * 64):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp)
            result = _publish_best_in_shot(
                cleared=candidates,
                input_path="/fake/input.mkv",
                time_base="1/1000",
                stream_index=0,
                stage=stage,
                ffmpeg_path="ffmpeg",
                ffprobe_path="ffprobe",
                color_filter=color_filter,
                **settings,
            )
            result["stage_artifacts"] = sorted(p.name for p in stage.glob("*.png"))
    return result


class TestPictureEnd:
    """A sample planned after the last video frame. The sampler plans the last
    shot to the container's stated length, which is its longest stream's."""

    # Batched: planned frames outweigh one decode of a short source.
    # Per-frame: a long source makes seeking cheaper than one decode.
    PATHS = [pytest.param(20.0, id="batched"), pytest.param(100_000.0, id="per-frame")]

    def _tail_shot(self, duration=20.0):
        """Two shots; the last shot's second sample sits 0.6 s before the
        stated end."""
        candidates = [
            _candidate(1, 90.0, shot=1, timestamp=duration - 15.0),
            _candidate(2, 90.0, shot=2, timestamp=duration - 3.0),
            _candidate(3, 90.0, shot=2, timestamp=duration - 0.6),
        ]
        frames = {
            "cand-0001": _png(_textured(3)),
            "cand-0002": _png(_textured(60)),
            "cand-0003": _png(_textured(61)),
        }
        return candidates, frames

    @pytest.mark.parametrize("duration", PATHS)
    def test_ordinary_tail_drops_the_sample(self, duration):
        """Audio outlasting the video by ~1 s is ordinary: the sample in that
        gap is dropped and recorded, the rest of the run is untouched."""
        candidates, frames = self._tail_shot(duration)
        last = duration - 1.3
        result = _run_publish(candidates, frames, last_frame=last,
                              source_duration=duration)

        published = {c.candidate_id for c in result["published"]}
        assert published == {"cand-0001", "cand-0002"}
        past = [o for o in result["redundant_omissions"]
                if o["reason"] == "past_the_last_frame"]
        assert [o["candidate"]["candidate_id"] for o in past] == ["cand-0003"]
        assert past[0]["picture_end_seconds"] == round(last, 6)
        assert "cand-0003.png" not in result["stage_artifacts"]

    @pytest.mark.parametrize("duration", PATHS)
    def test_picture_stopping_early_is_refused(self, duration):
        """A picture that ends far before the stated length is a damaged or
        cut-short file. The message must read as a refusal: the worker ends
        the stage as ineligible on that word instead of retrying it."""
        candidates, frames = self._tail_shot(duration)
        last = duration - 9.0
        with pytest.raises(RuntimeError, match=r"^refused: the picture stops at "):
            _run_publish(candidates, frames, last_frame=last,
                         source_duration=duration)

    def test_nothing_changes_when_every_sample_has_a_frame(self):
        candidates, frames = self._tail_shot()
        plain = _run_publish(candidates, frames, source_duration=20.0)
        with_end = _run_publish(candidates, frames, last_frame=19.9,
                                source_duration=20.0)
        assert ([c.candidate_id for c in plain["published"]]
                == [c.candidate_id for c in with_end["published"]])
        assert plain["redundant_omissions"] == with_end["redundant_omissions"]


class TestBestInShot:
    def test_one_shot_publishes_one_frame(self):
        """The defect this fixes: three samples of one shot published as twins."""
        candidates = [
            _candidate(1, 100.0, shot=10, timestamp=48.5),
            _candidate(2, 100.0, shot=10, timestamp=50.4),
            _candidate(3, 100.0, shot=10, timestamp=52.4),
        ]
        frames = {
            "cand-0001": _png(_twin_of(1, 4)),
            "cand-0002": _png(_textured(1)),
            "cand-0003": _png(_twin_of(1, 9)),
        }

        result = _run_publish(candidates, frames)

        assert len(result["published"]) == 1
        assert result["extraction_attempts"] == 3
        assert len(result["redundant_omissions"]) == 2
        assert all(
            omission["reason"] == "same_shot_near_twin"
            for omission in result["redundant_omissions"]
        )
        assert result["stage_artifacts"] == [
            f"{result['published'][0].candidate_id}.png"
        ]

    def test_distinct_second_frame_in_shot_survives(self):
        """A pan or an effect entering earns a second slot; micro-motion does not."""
        candidates = [
            _candidate(1, 90.0, shot=46, timestamp=10.0),
            _candidate(2, 90.0, shot=46, timestamp=11.0),
            _candidate(3, 90.0, shot=46, timestamp=12.0),
        ]
        frames = {
            "cand-0001": _png(_textured(2)),
            "cand-0002": _png(_twin_of(2, 6)),
            "cand-0003": _png(_textured(40)),
        }

        result = _run_publish(candidates, frames)

        published = {c.candidate_id for c in result["published"]}
        assert len(published) == 2
        assert "cand-0003" in published
        assert len(result["redundant_omissions"]) == 1
        assert result["shot_audit"][0]["published"] == 2

    def test_mid_luma_soft_frame_is_never_rejected(self):
        """research/01 warns against a single sharpness cutoff: anime uses flat
        fields, diffusion, and soft focus deliberately. The floor only fires on
        luma-extreme frames, so a well-exposed soft image is structurally safe
        however little edge energy it carries."""
        from frame_sample.frame_quality import CLEARED, analyze, floor_disposition

        soft = analyze(_png(_flat(128)))
        assert soft.quality.detail < DEFAULT_MIN_DETAIL
        assert soft.quality.entropy < DEFAULT_MIN_ENTROPY
        assert floor_disposition(soft.quality)[0] == CLEARED

        candidates = [_candidate(1, 90.0, shot=1, timestamp=1.0)]
        result = _run_publish(candidates, {"cand-0001": _png(_flat(128))})
        assert [c.candidate_id for c in result["published"]] == ["cand-0001"]
        assert result["quality_omissions"] == []

    @pytest.mark.parametrize("level,side", [(4, "near-black"), (250, "near-white")])
    def test_luma_extreme_blanks_are_still_rejected(self, level, side):
        from frame_sample.frame_quality import BLANK, analyze, floor_disposition

        blank = analyze(_png(_flat(level)))
        disposition, reason = floor_disposition(blank.quality)
        assert disposition == BLANK
        assert side in reason

    def test_shot_publish_limit_caps_a_shot_at_two(self):
        """Policy is one frame per shot plus a distinct second -- never a third."""
        candidates = [
            _candidate(i, 90.0, shot=46, timestamp=float(i)) for i in range(1, 4)
        ]
        frames = {
            "cand-0001": _png(_textured(50)),
            "cand-0002": _png(_textured(70)),
            "cand-0003": _png(_textured(90)),
        }

        result = _run_publish(candidates, frames)

        assert len(result["published"]) == 2
        assert result["shot_audit"][0]["published"] == 2
        assert [o["reason"] for o in result["redundant_omissions"]] == [
            "shot_publish_limit"
        ]
        assert result["stage_artifacts"] == sorted(
            f"{c.candidate_id}.png" for c in result["published"]
        )

    def test_quality_floor_rejects_fades_and_blank_cards(self):
        candidates = [
            _candidate(1, 124.4, shot=43, timestamp=1.0),
            _candidate(2, 80.0, shot=44, timestamp=5.0),
        ]
        frames = {
            "cand-0001": _png(_flat(2)),
            "cand-0002": _png(_textured(3)),
        }

        result = _run_publish(candidates, frames)

        assert [c.candidate_id for c in result["published"]] == ["cand-0002"]
        assert len(result["quality_omissions"]) == 1
        omission = result["quality_omissions"][0]
        assert omission["candidate"]["candidate_id"] == "cand-0001"
        assert "detail" in omission["reason"]
        assert result["stage_artifacts"] == ["cand-0002.png"]

    def test_top_scoring_fade_does_not_consume_a_slot(self):
        """scene_score ranks the cut, not the frame; a fade must not outrank content."""
        candidates = [
            _candidate(1, 124.4, shot=43, timestamp=1.0),
            _candidate(2, 70.0, shot=44, timestamp=5.0),
        ]
        frames = {
            "cand-0001": _png(_flat(255)),
            "cand-0002": _png(_textured(4)),
        }

        result = _run_publish(candidates, frames, max_candidates=1)

        assert [c.candidate_id for c in result["published"]] == ["cand-0002"]

    def test_shot_with_no_surviving_sample_is_recorded(self):
        candidates = [_candidate(1, 50.0, shot=7, timestamp=1.0)]
        frames = {"cand-0001": _png(_flat(0))}

        result = _run_publish(candidates, frames)

        assert result["published"] == []
        assert result["shot_audit"] == [{
            "shot_id": "shot-007",
            "sampled": 1,
            "published": 0,
            "outcome": "every_sample_was_blank",
        }]

    def test_a_lit_subject_in_a_void_is_published_for_review(self):
        """The floor's 43% false-reject rate was one test carrying three
        verdicts. Only a genuine blank is deleted now; anything else it
        questions is published and labelled."""
        from frame_sample.frame_quality import REVIEW

        candidates = [_candidate(1, 90.0, shot=1, timestamp=1.0)]
        result = _run_publish(candidates, {"cand-0001": _png(_silhouette(0, 255))})

        assert [c.candidate_id for c in result["published"]] == ["cand-0001"]
        assert result["quality_omissions"] == []
        quality = result["published_frame_quality"]["cand-0001"]
        assert quality["floor_disposition"] == REVIEW
        assert "kept for review" in quality["floor_reason"]
        assert result["shot_audit"][0]["outcome"] == "published"

    def test_symmetry_is_measured_on_every_published_frame(self):
        """Geometry is cheap, deterministic and model-free, so it belongs on
        this worker. The verdict does not -- telling a symmetric title card
        from a symmetric composition needs the tagger."""
        candidates = [
            _candidate(1, 90.0, shot=1, timestamp=1.0),
            _candidate(2, 80.0, shot=2, timestamp=9.0),
        ]
        frames = {
            # A flat field mirrors perfectly; a textured one does not.
            "cand-0001": _png(_silhouette(0, 255)),
            "cand-0002": _png(_textured(5)),
        }

        result = _run_publish(candidates, frames)

        measured = result["published_frame_quality"]
        assert measured["cand-0001"]["symmetry"] > 0.95
        assert measured["cand-0002"]["symmetry"] < 0.95
        # Tilt is recorded but never proposed as a label: in an anime close-up
        # the dominant orientation is the subject's contours, not camera roll.
        assert -45.0 <= measured["cand-0001"]["tilt_degrees"] <= 45.0

    def test_symmetry_survives_a_squeezed_aspect(self):
        """The 16:9 quality grid biases `detail`; it cannot bias this. Mirroring
        is about the vertical axis and resampling is separable, so a vertical
        stretch treats column x and column W-1-x identically."""
        from frame_sample.composition import measure

        square = measure(_png(_silhouette(0, 255)))
        assert square.is_symmetrical

    def test_a_cleared_frame_carries_no_review_reason(self):
        from frame_sample.frame_quality import CLEARED

        candidates = [_candidate(1, 90.0, shot=1, timestamp=1.0)]
        result = _run_publish(candidates, {"cand-0001": _png(_textured(5))})

        quality = result["published_frame_quality"]["cand-0001"]
        assert quality["floor_disposition"] == CLEARED
        assert "floor_reason" not in quality

    def test_a_shot_holding_only_a_silhouette_is_no_longer_lost(self):
        """One film lost 25 shots outright to the old floor and nine of them held
        a frame worth keeping. A shot dies now only if every sample is blank."""
        candidates = [
            _candidate(1, 90.0, shot=1, timestamp=1.0),
            _candidate(2, 80.0, shot=2, timestamp=9.0),
        ]
        frames = {
            "cand-0001": _png(_silhouette(0, 255)),
            "cand-0002": _png(_flat(1)),
        }

        result = _run_publish(candidates, frames)

        outcomes = {s["shot_id"]: s["outcome"] for s in result["shot_audit"]}
        assert outcomes["shot-001"] == "published"
        assert outcomes["shot-002"] == "every_sample_was_blank"
        assert [o["disposition"] for o in result["quality_omissions"]] == ["blank"]

    def test_the_blank_cut_is_the_only_thing_separating_the_two(self):
        """Same frame, same luma, same featurelessness -- the verdict turns on
        luma_stddev alone, which is what the labelled rejects supported."""
        from frame_sample.frame_quality import BLANK, REVIEW, analyze, floor_disposition

        silhouette = analyze(_png(_silhouette(0, 255))).quality
        assert floor_disposition(silhouette)[0] == REVIEW
        # Raise the cut above the frame's measured spread and it reads as blank.
        assert floor_disposition(
            silhouette, blank_luma_stddev=silhouette.luma_stddev
        )[0] == BLANK

    def test_review_frames_rank_below_cleared_ones_inside_a_shot(self):
        """A review frame competes rather than pre-empts. Detail carries the
        within-shot representative score and a questioned frame has little, so
        where a shot holds anything cleared, that is what represents it."""
        candidates = [
            _candidate(1, 90.0, shot=1, timestamp=1.0),
            _candidate(2, 90.0, shot=1, timestamp=3.0),
        ]
        frames = {
            "cand-0001": _png(_silhouette(0, 255)),
            "cand-0002": _png(_textured(5)),
        }

        result = _run_publish(candidates, frames, max_per_shot=1)

        assert [c.candidate_id for c in result["published"]] == ["cand-0002"]

    def test_a_breadth_dropped_review_frame_keeps_its_evidence(self):
        """An adversarial review caught this: the pixels went and so did the
        only record the frame had ever been questioned. A rejection a reviewer
        cannot reconstruct is not evidence."""
        from frame_sample.frame_quality import REVIEW

        candidates = [
            _candidate(1, 90.0, shot=1, timestamp=1.0),
            _candidate(2, 80.0, shot=2, timestamp=9.0),
        ]
        frames = {
            "cand-0001": _png(_textured(5)),
            "cand-0002": _png(_silhouette(0, 255)),
        }

        result = _run_publish(candidates, frames, max_candidates=1)

        assert [c.candidate_id for c in result["breadth_omitted"]] == ["cand-0002"]
        dropped = result["breadth_measured"]["cand-0002"]
        assert dropped["frame_quality"]["floor_disposition"] == REVIEW
        assert "kept for review" in dropped["frame_quality"]["floor_reason"]
        assert dropped["displaced_by_candidate_id"] == "cand-0001"
        assert 0.0 <= dropped["framing_distance"] <= 1.0

    def test_breadth_omitted_pixels_are_kept_beside_the_bundle(self):
        """The decode is already paid for. A frame that cleared the floor, is
        visually distinct, and lost only on capacity is exactly what a later
        curation pass wants, so it is moved rather than deleted."""
        candidates = [
            _candidate(1, 90.0, shot=1, timestamp=1.0),
            _candidate(2, 80.0, shot=2, timestamp=9.0),
        ]
        frames = {
            "cand-0001": _png(_textured(5)),
            "cand-0002": _png(_textured(40)),
        }

        with tempfile.TemporaryDirectory() as tmp:
            surplus = Path(tmp) / "surplus" / "bundle-1"
            result = _run_publish(candidates, frames, max_candidates=1,
                                  surplus=surplus)
            dropped = [c.candidate_id for c in result["breadth_omitted"]]
            assert len(dropped) == 1
            assert sorted(p.name for p in surplus.iterdir()) == [
                f"{dropped[0]}.png"
            ]
        # The bundle itself still carries only the published frame.
        assert result["stage_artifacts"] == [
            f"{result['published'][0].candidate_id}.png"
        ]

    def test_the_blank_cut_is_bounded_by_the_labelled_evidence(self):
        """The one flag that decides whether pixels are destroyed. Above the
        lowest luma_stddev ever measured on a labelled keeper it would delete
        frames a reader has already said belonged in the library."""
        from frame_sample.frame_quality import (
            DEFAULT_BLANK_LUMA_STDDEV, LOWEST_LABELLED_KEEPER_STDDEV,
            MAX_BLANK_LUMA_STDDEV, FrameQuality, floor_disposition,
        )
        from frame_sample.pipeline_cli import main

        assert DEFAULT_BLANK_LUMA_STDDEV < MAX_BLANK_LUMA_STDDEV
        # The bound must round DOWN from the keeper, not to it: deletion is on
        # `<=`, so a bound equal-or-above it deletes the frame that justified
        # it. This is the assertion that was missing when that shipped.
        keeper = FrameQuality(luma_mean=0.99, luma_stddev=LOWEST_LABELLED_KEEPER_STDDEV,
                              detail=0.0008, entropy=1.027)
        assert MAX_BLANK_LUMA_STDDEV < LOWEST_LABELLED_KEEPER_STDDEV
        assert floor_disposition(
            keeper, blank_luma_stddev=MAX_BLANK_LUMA_STDDEV
        )[0] != "blank"
        required = [
            "--input", "x", "--output-root", "y", "--source-identity", "z",
            "--bundle-id", "b", "--job-id", "j",
            "--source-fingerprint-ref", "sha256:" + "0" * 64,
        ]
        for bad in ("0.5", "-0.001", str(MAX_BLANK_LUMA_STDDEV + 0.001)):
            assert main(required + ["--blank-luma-stddev", bad]) == 2

    def test_breadth_selection_is_disposition_blind(self):
        """Pinned because it is a deliberate choice, not an oversight. Once a
        frame is measured it competes on visual distinctness alone, so a review
        frame can take a budget slot from a cleared one. The alternative --
        letting cleared frames fill the budget first -- would re-create the old
        floor's loss through a different door, since on the title the floor hurt
        most the questioned frames *are* the episode."""
        candidates = [
            _candidate(1, 90.0, shot=1, timestamp=1.0),
            _candidate(2, 80.0, shot=2, timestamp=9.0),
        ]
        frames = {
            "cand-0001": _png(_silhouette(0, 255)),
            "cand-0002": _png(_textured(5)),
        }

        result = _run_publish(candidates, frames, max_candidates=1)

        # The higher scene_score seeds the greedy selection regardless of how
        # the floor dispositioned it.
        assert [c.candidate_id for c in result["published"]] == ["cand-0001"]

    def test_repeated_framing_across_shots_is_dropped(self):
        candidates = [
            _candidate(1, 90.0, shot=1, timestamp=1.0),
            _candidate(2, 80.0, shot=2, timestamp=9.0),
        ]
        frames = {
            "cand-0001": _png(_textured(5)),
            "cand-0002": _png(_twin_of(5, 3)),
        }

        result = _run_publish(candidates, frames)

        assert [c.candidate_id for c in result["published"]] == ["cand-0001"]
        assert result["redundant_omissions"][0]["reason"] == "repeated_framing"
        assert result["redundant_omissions"][0]["selected_candidate_id"] == "cand-0001"

    def test_higher_detail_sample_wins_the_shot(self):
        candidates = [
            _candidate(1, 90.0, shot=3, timestamp=1.0),
            _candidate(2, 90.0, shot=3, timestamp=2.0),
        ]
        # A soft gradient carries far less edge energy than a fine pattern.
        frames = {
            "cand-0001": _png(lambda x, y: (x * 4 % 256, x * 4 % 256, x * 4 % 256)),
            "cand-0002": _png(_textured(6)),
        }

        result = _run_publish(candidates, frames, intra_shot_distinct=2.0)

        assert [c.candidate_id for c in result["published"]] == ["cand-0002"]

    def test_output_is_chronological(self):
        candidates = [
            _candidate(1, 60.0, shot=1, timestamp=40.0),
            _candidate(2, 90.0, shot=2, timestamp=10.0),
            _candidate(3, 75.0, shot=3, timestamp=25.0),
        ]
        frames = {
            "cand-0001": _png(_textured(7)),
            "cand-0002": _png(_textured(21)),
            "cand-0003": _png(_textured(35)),
        }

        result = _run_publish(candidates, frames)

        timestamps = [c.timestamp_seconds for c in result["published"]]
        assert timestamps == [10.0, 25.0, 40.0]

    def test_shots_are_taken_in_scene_score_order(self):
        candidates = [
            _candidate(1, 50.0, shot=1, timestamp=10.0),
            _candidate(2, 99.0, shot=2, timestamp=20.0),
            _candidate(3, 70.0, shot=3, timestamp=30.0),
        ]
        frames = {
            "cand-0001": _png(_textured(8)),
            "cand-0002": _png(_textured(22)),
            "cand-0003": _png(_textured(36)),
        }

        result = _run_publish(candidates, frames, max_candidates=2)

        # Ranking decides what is EXAMINED, in score order. Which of the
        # measured frames is published is then decided by distinctness, so
        # the published set is no longer the top of the score order.
        assert result["extraction_attempts"] == 3, "all three examined"
        assert len(result["published"]) == 2
        assert {c.candidate_id for c in result["published"]} <= {
            "cand-0001", "cand-0002", "cand-0003"}

    def test_capacity_limit_stops_publishing(self):
        # The pool must exceed the WIDENED measure budget for the cap to bite:
        # extraction now deliberately measures more frames than it publishes.
        # Widening now measures the whole plan, so the pool has to exceed
        # MAX_MEASURED_CEILING for capacity to bite at all. Drive the ceiling
        # directly rather than inflating the fixture to 4,000 frames.
        candidates = [
            _candidate(i, float(100 - i), shot=i, timestamp=float(i)) for i in range(1, 20)
        ]
        frames = {f"cand-{i:04d}": _png(_textured(i * 9)) for i in range(1, 20)}

        with patch("frame_sample.pipeline_cli.MAX_MEASURED_CEILING", 8):
            result = _run_publish(candidates, frames, max_candidates=3)

        assert len(result["published"]) == 3
        assert result["stop_reason"] == "prepared_bundle_candidate_limit"
        assert result["capacity_omitted"], "the cap must still stop the loop"
        assert result["unprocessed"] == []

    def test_extraction_limit_defers_remaining_shots(self):
        candidates = [
            _candidate(i, float(100 - i), shot=i, timestamp=float(i)) for i in range(1, 11)
        ]
        frames = {f"cand-{i:04d}": _png(_textured(i * 9)) for i in range(1, 11)}

        result = _run_publish(candidates, frames, max_extractions=4)

        assert result["extraction_attempts"] == 4
        assert result["stop_reason"] == "outside_the_extraction_plan"
        assert len(result["published"]) == 4
        assert len(result["unprocessed"]) == 6
        assert result["capacity_omitted"] == []

    def test_omission_buckets_stay_disjoint(self):
        candidates = [
            _candidate(1, 100.0, shot=1, timestamp=1.0),
            _candidate(2, 99.0, shot=1, timestamp=2.0),
            _candidate(3, 98.0, shot=2, timestamp=3.0),
            _candidate(4, 97.0, shot=3, timestamp=4.0),
            _candidate(5, 96.0, shot=4, timestamp=5.0),
        ]
        frames = {
            "cand-0001": _png(_textured(11)),
            "cand-0002": _png(_twin_of(11, 5)),
            "cand-0003": _png(_flat(8)),
            "cand-0004": _png(_textured(29)),
            "cand-0005": _png(_textured(47)),
        }

        result = _run_publish(candidates, frames, max_candidates=2)

        published = {c.candidate_id for c in result["published"]}
        redundant = {o["candidate"]["candidate_id"] for o in result["redundant_omissions"]}
        low_quality = {o["candidate"]["candidate_id"] for o in result["quality_omissions"]}
        capacity = {c.candidate_id for c in result["capacity_omitted"]}
        unprocessed = {c.candidate_id for c in result["unprocessed"]}

        buckets = [published, redundant, low_quality, capacity, unprocessed]
        for i in range(len(buckets)):
            for j in range(i + 1, len(buckets)):
                assert buckets[i] & buckets[j] == set()

    def test_only_published_artifacts_remain_staged(self):
        candidates = [
            _candidate(1, 100.0, shot=1, timestamp=1.0),
            _candidate(2, 99.0, shot=1, timestamp=2.0),
            _candidate(3, 98.0, shot=2, timestamp=3.0),
        ]
        frames = {
            "cand-0001": _png(_textured(12)),
            "cand-0002": _png(_twin_of(12, 5)),
            "cand-0003": _png(_flat(250)),
        }

        result = _run_publish(candidates, frames)

        assert result["stage_artifacts"] == sorted(
            f"{c.candidate_id}.png" for c in result["published"]
        )

    def test_published_quality_is_recorded_for_audit(self):
        candidates = [_candidate(1, 90.0, shot=1, timestamp=1.0)]
        frames = {"cand-0001": _png(_textured(13))}

        result = _run_publish(candidates, frames)

        quality = result["published_frame_quality"]["cand-0001"]
        assert set(quality) == {
            "luma_mean", "luma_stddev", "detail", "entropy", "floor_disposition",
            # Composition geometry rides with the quality record.
            "symmetry", "symmetrical", "tilt_degrees", "tilt_coherence",
        }
        assert quality["detail"] >= DEFAULT_MIN_DETAIL


@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_max_extractions_rejected(value):
    from frame_sample.pipeline_cli import main

    with tempfile.TemporaryDirectory() as tmp:
        identity_path = Path(tmp) / "identity.json"
        identity_path.write_text(json.dumps({
            "series_title": "Test", "entry_type": "episode",
            "source_filename": "test.mkv", "series_id": 1,
            "episode_ids": [1], "file_id": 1,
            "work_id": "t",
        }))
        output_root = Path(tmp) / "output"
        output_root.mkdir()
        rc = main([
            "--input", "/nonexistent.mkv",
            "--output-root", str(output_root),
            "--source-identity", str(identity_path),
            "--bundle-id", "b", "--job-id", "j",
            "--source-fingerprint-ref", "f",
            "--max-extractions", value,
        ])
        assert rc == 2


class TestBatchExtraction:
    """The batch path maps output files to candidates positionally, so its
    guards are what stop frames being silently mislabelled."""

    def test_unsorted_input_is_refused(self):
        from frame_sample.pipeline_cli import _extract_batch
        with pytest.raises(RuntimeError, match="sorted by pts"):
            _extract_batch("/fake.mkv", [("cand-0002", 900), ("cand-0001", 100)],
                           Path("/tmp"), "ffmpeg", "null")

    def test_empty_batch_is_a_no_op(self):
        from frame_sample.pipeline_cli import _extract_batch
        assert _extract_batch("/fake.mkv", [], Path("/tmp"), "ffmpeg", "null") == {}

    def test_a_pts_mismatch_raises_rather_than_mislabelling(self):
        """If ffmpeg emits a different set than requested, positional mapping
        would attach the wrong pixels to a candidate id."""
        from frame_sample import pipeline_cli

        class FakeResult:
            stderr = "showinfo pts: 100 ... showinfo pts: 999"

        with patch.object(pipeline_cli, "_run", return_value=FakeResult()):
            with pytest.raises(RuntimeError, match="PTS sequences differ"):
                pipeline_cli._extract_batch(
                    "/fake.mkv", [("cand-0001", 100), ("cand-0002", 200)],
                    Path("/tmp"), "ffmpeg", "null",
                )


class TestExtractionPlanning:
    """A 200-frame budget once planned 631 extractions on a real episode, and
    the default budget of 8 would have planned up to max_extractions."""

    def _shots(self, count, samples_each=2):
        from frame_sample.pipeline_cli import _rank_shots
        candidates = []
        index = 0
        for shot in range(count):
            for _ in range(samples_each):
                index += 1
                candidates.append(
                    _candidate(index, float(1000 - shot), shot=shot,
                               timestamp=float(index))
                )
        return _rank_shots(candidates)

    def test_plan_is_bounded_by_the_publish_budget(self):
        from frame_sample.pipeline_cli import _plan_extractions
        planned, deferred, stopped_by = _plan_extractions(
            self._shots(326, 2), max_candidates=8, max_extractions=800
        )
        assert len(planned) <= 8 * 2 * 2, "a budget of 8 must not plan hundreds"
        assert deferred, "the rest belong in the deferred bucket"
        assert stopped_by == "shot_plan_budget"

    def test_plan_still_respects_the_attempt_cap(self):
        from frame_sample.pipeline_cli import _plan_extractions
        planned, _, stopped_by = _plan_extractions(
            self._shots(326, 2), max_candidates=300, max_extractions=20
        )
        assert len(planned) <= 20
        # Both bounds bind here; the truncating one has to win the report, or a
        # run cut short of its measurement target reads as one that finished.
        assert stopped_by == "extraction_attempt_limit"

    def test_plan_carries_headroom_over_the_budget(self):
        """Some shots publish nothing, so planning exactly the budget would
        under-fill it."""
        from frame_sample.pipeline_cli import _plan_extractions
        planned, _, stopped_by = _plan_extractions(
            self._shots(326, 1), max_candidates=100, max_extractions=800
        )
        assert len(planned) > 100
        assert stopped_by == "shot_plan_budget"

    def test_batching_is_chosen_only_when_it_beats_seeking(self):
        from frame_sample.pipeline_cli import _should_batch
        # A whole episode for a handful of frames: seek instead.
        assert _should_batch(planned=19, source_duration=1440.0) is False
        # Enough frames that per-frame seeking would out-decode the file.
        assert _should_batch(planned=480, source_duration=1440.0) is True

    def test_unknown_duration_does_not_batch(self):
        from frame_sample.pipeline_cli import _should_batch
        assert _should_batch(planned=1000, source_duration=0.0) is False

    def test_small_budget_uses_per_frame_seeks(self):
        """The regression in full: with the default budget the batched path
        dragged an entire episode through the decoder for a few frames."""
        candidates = [
            _candidate(i, float(100 - i), shot=i, timestamp=float(i * 10))
            for i in range(1, 13)
        ]
        frames = {f"cand-{i:04d}": _png(_textured(i * 7)) for i in range(1, 13)}
        result = _run_publish(candidates, frames, max_candidates=3,
                              source_duration=3600.0)
        assert len(result["published"]) == 3
        # Still bounded -- the regression this guards decoded an entire
        # episode for a few frames. The bound is now the WIDENED budget,
        # because choosing well needs more measured frames than it keeps.
        from frame_sample.pipeline_cli import (
            EXTRACTION_WIDENING, SHOT_PLAN_HEADROOM)
        ceiling = 3 * EXTRACTION_WIDENING * SHOT_PLAN_HEADROOM
        assert result["extraction_attempts"] <= ceiling, (
            "the plan must stay inside the widened budget"
        )
        # The regression this guards was the BATCHED path decoding a whole
        # 3,600-second episode to collect a few frames. That is a path choice,
        # not a frame count -- and since widening became full-pool coverage the
        # count is deliberately the whole pool. Assert the path.
        from frame_sample.pipeline_cli import _should_batch
        assert _should_batch(len(candidates), 3600.0) is False, (
            "a handful of frames across an hour must seek, not decode it all"
        )


class TestBatchChunking:
    """486 eq() terms in one select expression failed to parse on a real
    episode after 268 s of work. Chunking is what stops that recurring."""

    def test_a_large_batch_is_split(self):
        from frame_sample import pipeline_cli
        wanted = [(f"cand-{i:04d}", i * 100) for i in range(1, 201)]
        calls = []

        def record(video_path, chunk, stage, ffmpeg_path, time_base,
                   color_filter, seek_preroll=1.0):
            calls.append(len(chunk))
            for candidate_id, _pts in chunk:
                (stage / f"{candidate_id}.png").write_bytes(b"x")

        with patch.object(pipeline_cli, "_extract_one_batch", side_effect=record):
            with tempfile.TemporaryDirectory() as tmp:
                got = pipeline_cli._extract_batch(
                    "/fake.mkv", wanted, Path(tmp), "ffmpeg", "null", "1/1000"
                )
        assert len(got) == 200
        assert max(calls) <= pipeline_cli.BATCH_CHUNK_SIZE
        assert sum(calls) == 200

    def test_chunk_size_stays_within_the_parser_limit(self):
        from frame_sample.pipeline_cli import BATCH_CHUNK_SIZE
        assert BATCH_CHUNK_SIZE <= 64, (
            "486 terms failed to parse; keep chunks far below that"
        )


class TestSeekMargin:
    """The seek margin exists to clear the container's start_time, not a GOP.

    ffmpeg adds start_time to an input -ss. A margin below it makes the seek
    overshoot the target, match nothing, and decode to end of file -- measured
    at 1m50s on the 4K feature, reading as a hang. The pipeline's own passage
    slice carries start_time 0.083, so zero cannot be assumed.
    """

    def _probe(self, payload):
        from frame_sample import pipeline_cli

        class FakeResult:
            stdout = payload

        return patch.object(pipeline_cli, "_run", return_value=FakeResult())

    def test_start_time_is_read_from_the_video_stream(self):
        from frame_sample import pipeline_cli
        with self._probe('{"streams": [{"start_time": "7.000000"}]}'):
            assert pipeline_cli._container_start_time("/f.mkv", "ffprobe") == 7.0

    def test_missing_or_unparseable_start_time_is_zero(self):
        from frame_sample import pipeline_cli
        for payload in ('{"streams": []}', '{"streams": [{}]}',
                        '{"streams": [{"start_time": "N/A"}]}'):
            with self._probe(payload):
                assert pipeline_cli._container_start_time("/f.mkv", "ffprobe") == 0.0

    def test_negative_start_time_never_shrinks_the_margin(self):
        from frame_sample import pipeline_cli
        with self._probe('{"streams": [{"start_time": "-3.5"}]}'):
            assert pipeline_cli._container_start_time("/f.mkv", "ffprobe") == 0.0

    def test_seek_clears_start_time_and_the_read_is_bounded(self):
        from frame_sample import pipeline_cli
        seen = {}

        class FakeResult:
            stderr = "showinfo pts: 60000"

        def capture(cmd, **kwargs):
            seen["cmd"] = cmd
            out = Path(cmd[-1])
            out.write_bytes(b"x")
            return FakeResult()

        start_time = 7.0
        preroll = start_time + pipeline_cli.SEEK_MARGIN_SECONDS
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(pipeline_cli, "_run", side_effect=capture):
                pipeline_cli._extract_exact_frame(
                    "/f.mkv", 60.0, 60000, Path(tmp) / "out.png", "ffmpeg",
                    "null", preroll,
                )
        cmd = seen["cmd"]
        seek = float(cmd[cmd.index("-ss") + 1])
        assert 60.0 - seek >= start_time, (
            "the margin must clear start_time or ffmpeg seeks past the target")
        assert "-to" in cmd, "an overshoot must not decode to end of file"
        assert float(cmd[cmd.index("-to") + 1]) > 60.0

    def test_batch_cost_model_is_not_the_seek_margin(self):
        from frame_sample.pipeline_cli import (
            BATCH_COST_SECONDS_PER_FRAME, SEEK_MARGIN_SECONDS, _should_batch)
        assert BATCH_COST_SECONDS_PER_FRAME != SEEK_MARGIN_SECONDS, (
            "sharing one constant is what invited a measured 2x regression")
        # 200 planned over a 1440 s episode batched before, and must still.
        assert _should_batch(200, 1440.0)


class TestTemporalSpread:
    """Ranking shots by scene_score alone makes the budget a score threshold,
    and the threshold rises with the length of the work: 51 on a 25-minute
    episode, 92 on a 155-minute feature. Four stretches totalling 42 minutes of
    that feature published nothing, including a quiet dialogue sequence."""

    def _shots(self, layout):
        """layout: list of (timestamp, score) -> one candidate per shot."""
        from frame_sample.sampler import CandidateRecord
        return [
            CandidateRecord(
                candidate_id=f"cand-{i:04d}", shot_id=f"shot-{i:04d}",
                timestamp_seconds=float(t), scene_score=float(s),
                exclusion_status="cleared",
            )
            for i, (t, s) in enumerate(layout)
        ]

    def _loud_cluster_and_quiet_span(self):
        """Ten loud cuts in the first 20 s, then quiet ones across an hour."""
        layout = [(i * 2.0, 100.0) for i in range(10)]
        layout += [(300.0 + i * 300.0, 40.0) for i in range(10)]
        return self._shots(layout)

    def test_alpha_zero_is_exactly_the_old_score_order(self):
        from frame_sample.pipeline_cli import _rank_shots
        cleared = self._loud_cluster_and_quiet_span()
        got = _rank_shots(cleared, 3600.0, 10, spread_alpha=0.0)
        expected = _rank_shots(cleared)  # no duration/budget -> score order
        assert [s for s, _ in got] == [s for s, _ in expected]
        assert [s for s, _ in got][:10] == [f"shot-{i:04d}" for i in range(10)], (
            "score order must still put every loud cut first"
        )

    def test_spread_reaches_the_quiet_span_within_the_budget(self):
        from frame_sample.pipeline_cli import _rank_shots
        cleared = self._loud_cluster_and_quiet_span()
        ranked = _rank_shots(cleared, 3600.0, 10, spread_alpha=0.5)
        top = [members[0].timestamp_seconds for _s, members in ranked[:10]]
        assert max(top) > 300.0, (
            "a 10-shot budget over an hour must not spend itself on 20 seconds"
        )
        assert sum(1 for t in top if t > 300.0) >= 5

    def test_a_high_score_still_wins_when_nothing_is_near(self):
        from frame_sample.pipeline_cli import _rank_shots
        cleared = self._shots([(0.0, 20.0), (600.0, 90.0), (1200.0, 55.0)])
        ranked = _rank_shots(cleared, 1800.0, 3, spread_alpha=0.5)
        assert ranked[0][1][0].scene_score == 90.0

    def test_every_shot_is_still_ranked_none_dropped(self):
        from frame_sample.pipeline_cli import _rank_shots
        cleared = self._loud_cluster_and_quiet_span()
        ranked = _rank_shots(cleared, 3600.0, 5, spread_alpha=0.5)
        assert len(ranked) == 20, "ranking orders shots, it never discards them"
        assert len({s for s, _ in ranked}) == 20

    def test_missing_duration_or_budget_falls_back_to_score_order(self):
        from frame_sample.pipeline_cli import _rank_shots
        cleared = self._loud_cluster_and_quiet_span()
        base = [s for s, _ in _rank_shots(cleared)]
        for duration, budget in ((0.0, 10), (3600.0, 0), (-1.0, 10)):
            assert [s for s, _ in _rank_shots(cleared, duration, budget)] == base


class TestDerivedBudget:
    """A flat budget made a 155-minute feature cover 9% of its shots against an
    episode's 53-85%, and measured runs show that is not where content runs out:
    the duplicate rate holds at 34-48% whether coverage is 9% or 85%."""

    def _target(self, shots):
        """The density alone. This is what research/00's bands describe."""
        from frame_sample.pipeline_cli import PUBLISH_FRAMES_PER_SHOT
        return round(shots * PUBLISH_FRAMES_PER_SHOT)

    def _derive(self, shots):
        """What a run actually publishes: the density plus review overhead."""
        from frame_sample.pipeline_cli import (
            BUDGET_FLOOR, MAX_PUBLISHED_CEILING, PUBLISH_FRAMES_PER_SHOT,
            REVIEW_OVERHEAD)
        return max(BUDGET_FLOOR, min(MAX_PUBLISHED_CEILING, round(
            shots * PUBLISH_FRAMES_PER_SHOT * REVIEW_OVERHEAD)))

    def test_the_overhead_buys_back_what_a_reviewer_deletes(self):
        """OP/ED is a roughly fixed ~10% per-work cost and is deliberately not
        solved automatically, so the budget carries it. An episode at 456 shots lands
        above research/00's episode band on purpose: the band never budgeted for
        frames the reviewer throws away."""
        for shots in (307, 397, 427, 456, 762, 1014):
            assert self._derive(shots) > self._target(shots)
            assert self._derive(shots) <= round(self._target(shots) * 1.15)

    def test_it_reproduces_researchs_episode_band(self):
        # Detected shot counts from six real episodes.
        for label, shots in (("a", 456), ("b", 397), ("c", 307),
                             ("d", 427), ("e", 280), ("f", 343)):
            got = self._target(shots)
            assert 35 <= got <= 70, f"{label}: {got} outside research/00's 35-70"

    def test_it_reproduces_researchs_film_band(self):
        for label, shots in (("a", 1014), ("b", 908), ("c", 762)):
            got = self._target(shots)
            assert 100 <= got <= 220, f"{label}: {got} outside research/00's 100-220"

    def test_the_outlier_feature_is_capped_not_unbounded(self):
        from frame_sample.pipeline_cli import MAX_PUBLISHED_CEILING
        # 2209 shots * 0.15 = 331, above research/00's ~300 cap.
        assert self._derive(2209) == MAX_PUBLISHED_CEILING

    def test_a_tiny_work_still_gets_a_usable_set(self):
        from frame_sample.pipeline_cli import BUDGET_FLOOR
        assert self._derive(10) == BUDGET_FLOOR
        assert self._derive(0) == BUDGET_FLOOR

    def test_budget_rises_with_shot_count_not_duration(self):
        # Two works of equal length but different cutting rates must differ.
        assert self._derive(800) > self._derive(400)

    def test_an_explicit_budget_still_wins(self):
        from frame_sample.pipeline_cli import _parse_args
        base = ["--input", "a.mkv", "--output-root", "o", "--source-identity", "i.json",
                "--bundle-id", "b", "--job-id", "j",
                "--source-fingerprint-ref", "sha1:x"]
        assert _parse_args(base).max_candidates is None, "omitted means derive"
        assert _parse_args(base + ["--max-candidates", "70"]).max_candidates == 70


class TestBreadthSelection:
    """Ranking could only ask how big the cut was, so a beautiful shot reached
    by a soft transition was never extracted: 44 of 88 candidates in one
    three-minute sequence, none of them quality-floor rejects. Extraction is
    now widened and the published set chosen from measured frames."""

    def _cands(self, n, scores=None, spacing=10.0):
        from frame_sample.sampler import CandidateRecord
        return [
            CandidateRecord(
                candidate_id=f"cand-{i:04d}", shot_id=f"shot-{i:04d}",
                timestamp_seconds=i * spacing,
                scene_score=(scores[i] if scores else float(100 - i)),
                exclusion_status="cleared",
            )
            for i in range(n)
        ]

    def test_it_keeps_the_budget_and_returns_the_rest(self):
        from frame_sample.pipeline_cli import _select_by_distinctness
        import numpy as np
        cands = self._cands(20)
        sigs = {c.candidate_id: np.full(16, i / 20.0, dtype=np.float32)
                for i, c in enumerate(cands)}
        kept, rest, displaced = _select_by_distinctness(cands, sigs, 6, 200.0, 0.5)
        assert len(kept) == 6
        assert len(rest) == 14
        assert not ({c.candidate_id for c in kept} & {c.candidate_id for c in rest})

    def test_a_small_pool_passes_through_untouched(self):
        from frame_sample.pipeline_cli import _select_by_distinctness
        cands = self._cands(4)
        sigs = {c.candidate_id: b"\x00" * 16 for c in cands}
        kept, rest, displaced = _select_by_distinctness(cands, sigs, 10, 100.0, 0.5)
        assert kept == cands and rest == []

    def test_it_prefers_a_visually_different_frame_over_a_higher_score(self):
        from frame_sample.pipeline_cli import _select_by_distinctness
        # Three near-identical high scorers, one distinct low scorer.
        cands = self._cands(4, scores={0: 99.0, 1: 98.0, 2: 97.0, 3: 10.0})
        import numpy as np
        same = np.zeros(16, dtype=np.float32)
        sigs = {"cand-0000": same, "cand-0001": same, "cand-0002": same,
                "cand-0003": np.ones(16, dtype=np.float32)}
        kept, _, _ = _select_by_distinctness(cands, sigs, 2, 100.0, 0.0)
        assert "cand-0003" in {c.candidate_id for c in kept}, (
            "the distinct frame must beat a near-twin of the top scorer"
        )

    def test_widening_survives_every_budget_including_the_ceiling(self):
        """This test previously asserted the opposite and enshrined a bug.

        Capping measurement with MAX_PUBLISHED_CEILING made a 220-frame budget
        widen 1.36x and the 4K feature's derived 300 widen 1.00x -- the
        mechanism did nothing precisely where it was argued to matter.
        """
        import math
        from frame_sample.pipeline_cli import (
            EXTRACTION_WIDENING, MAX_MEASURED_CEILING, MAX_PUBLISHED_CEILING)
        assert MAX_MEASURED_CEILING > MAX_PUBLISHED_CEILING, (
            "how many frames may be looked at is not how many may be published"
        )
        for budget in (35, 49, 60, 152, 220, MAX_PUBLISHED_CEILING):
            measured = min(MAX_MEASURED_CEILING,
                           math.ceil(budget * EXTRACTION_WIDENING))
            # Either the full multiple applies, or the ceiling is what stopped
            # it. What must never happen again is widening quietly collapsing
            # toward 1.0x, which is what capping on the PUBLISH ceiling did.
            full = measured >= budget * EXTRACTION_WIDENING - 1
            assert full or measured == MAX_MEASURED_CEILING, (
                f"budget {budget} widened only {measured / budget:.2f}x and "
                f"the ceiling is not what stopped it"
            )
            assert measured / budget > 2.0, (
                f"budget {budget} widened only {measured / budget:.2f}x"
            )

    def test_widening_is_a_real_multiple(self):
        from frame_sample.pipeline_cli import EXTRACTION_WIDENING
        assert EXTRACTION_WIDENING > 1.0, (
            "at 1.0 the published set is again whatever ranking extracted, "
            "which is the failure this exists to fix"
        )


class TestBreadthAuditIntegrity:
    """Found in review, 2026-07-25: breadth omission deleted frames
    that duplicate records pointed at, and its count went unrecorded, so the
    selection partition silently stopped balancing."""

    def test_the_partition_balances_when_widening_discards(self):
        candidates = [
            _candidate(i, float(100 - i), shot=i, timestamp=float(i * 3))
            for i in range(1, 30)
        ]
        frames = {f"cand-{i:04d}": _png(_textured(i * 5)) for i in range(1, 30)}

        result = _run_publish(candidates, frames, max_candidates=4,
                              source_duration=120.0)

        accounted = (
            len(result["published"])
            + len(result["breadth_omitted"])
            + len(result["redundant_omissions"])
            + len(result["quality_omissions"])
            + len(result["capacity_omitted"])
            + len(result["unprocessed"])
        )
        assert accounted == len(candidates), (
            f"{len(candidates)} selected but {accounted} accounted for; "
            "every candidate must land in exactly one disposition"
        )

    def test_no_duplicate_record_points_at_a_frame_that_was_dropped(self):
        candidates = [
            _candidate(i, float(100 - i), shot=i, timestamp=float(i * 3))
            for i in range(1, 30)
        ]
        frames = {f"cand-{i:04d}": _png(_textured(i * 5)) for i in range(1, 30)}

        result = _run_publish(candidates, frames, max_candidates=4,
                              source_duration=120.0)

        published = {c.candidate_id for c in result["published"]}
        for omission in result["redundant_omissions"]:
            represented_by = omission.get("selected_candidate_id")
            if not represented_by:
                continue
            assert represented_by in published, (
                f"{omission['candidate']['candidate_id']} is justified by "
                f"{represented_by}, which is not in the bundle"
            )

    def test_dropped_frames_leave_no_pixels_behind(self):
        candidates = [
            _candidate(i, float(100 - i), shot=i, timestamp=float(i * 3))
            for i in range(1, 30)
        ]
        frames = {f"cand-{i:04d}": _png(_textured(i * 5)) for i in range(1, 30)}

        result = _run_publish(candidates, frames, max_candidates=4,
                              source_duration=120.0)

        assert result["breadth_omitted"], "this case must exercise widening"
        staged = set(result["stage_artifacts"])
        for candidate in result["breadth_omitted"]:
            assert f"{candidate.candidate_id}.png" not in staged
            assert candidate.candidate_id not in result["extracted_candidates"]
            assert candidate.candidate_id not in result["published_frame_quality"]


class TestSelectorCost:
    """The first implementation recomputed every prior distance each round,
    making it O(N*K^2) -- about 13 million signature comparisons at a
    300-frame budget over a 900-frame pool."""

    def test_distance_calls_scale_linearly_in_the_budget(self):
        import numpy as np
        from frame_sample import pipeline_cli
        from frame_sample.sampler import CandidateRecord

        calls = {"n": 0}
        real = pipeline_cli.frame_quality.framing_distance

        def counting(a, b):
            calls["n"] += 1
            return real(a, b)

        pool_size = 120
        cands = [
            CandidateRecord(candidate_id=f"cand-{i:04d}", shot_id=f"shot-{i:04d}",
                            timestamp_seconds=float(i), scene_score=float(200 - i),
                            exclusion_status="cleared")
            for i in range(pool_size)
        ]
        sigs = {c.candidate_id: np.full(8, i / pool_size, dtype=np.float32)
                for i, c in enumerate(cands)}

        with patch.object(pipeline_cli.frame_quality, "framing_distance",
                          side_effect=counting):
            pipeline_cli._select_by_distinctness(cands, sigs, 40, 500.0, 0.5)

        # O(N*K) is at most pool_size per chosen frame; the quadratic version
        # ran roughly K/2 times that.
        assert calls["n"] <= pool_size * 40, (
            f"{calls['n']} distance calls for a 40-frame budget over "
            f"{pool_size} candidates is not linear in the budget"
        )
