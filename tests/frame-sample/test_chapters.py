"""Opening and ending intervals from chapter markers (#47)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from frame_sample.chapters import chapter_intervals, classify_chapter, probe_chapters  # noqa: E402

from conftest import ffmpeg_available, generate_synthetic_video  # noqa: E402


def chapter(title, start, end):
    return {"title": title, "start": float(start), "end": float(end)}


class TestNames:
    def test_named_openings_and_endings_are_held_whatever_their_length(self):
        assert classify_chapter(chapter("OP", 38, 128), 1420) == ("opening_theme", "high", "named")
        assert classify_chapter(chapter("Opening", 0, 60), 1420) == ("opening_theme", "high", "named")
        assert classify_chapter(chapter("ED", 1290, 1380), 1420) == ("ending_theme", "high", "named")
        assert classify_chapter(chapter("Ending", 1200, 1355), 1420) == ("ending_theme", "high", "named")
        assert classify_chapter(chapter("Outro", 1300, 1400), 1420) == ("ending_theme", "high", "named")
        assert classify_chapter(chapter("Credits Start", 1291, 1381), 1420) == ("ending_theme", "high", "named")
        assert classify_chapter(chapter("op 2", 100, 190), 1420)[0] == "opening_theme"

    def test_the_cold_open_names_never_count_by_name(self):
        # "Intro" is the cold open in one release group and the opening in
        # another, so only its length and position can say.
        assert classify_chapter(chapter("Intro", 0, 190), 1420) is None
        assert classify_chapter(chapter("Avant", 0, 60), 1420) is None
        assert classify_chapter(chapter("Prologue", 0, 180), 1420) is None
        assert classify_chapter(chapter("Intro", 333, 420), 1420) == ("opening_theme", "medium", "length-and-position")

    def test_a_named_marker_too_short_to_be_a_theme_is_ignored(self):
        assert classify_chapter(chapter("OP", 100, 100), 1420) is None
        assert classify_chapter(chapter("Opening", 180, 188), 1420) is None

    def test_story_chapters_are_not_held(self):
        assert classify_chapter(chapter("Part A", 128, 700), 1420) is None
        assert classify_chapter(chapter("Episode", 188, 1291), 1420) is None
        assert classify_chapter(chapter("Next", 1380, 1410), 1420) is None
        assert classify_chapter(chapter("", 0, 58), 1420) is None


class TestLengthAndPosition:
    def test_a_ninety_second_chapter_near_either_end_is_held(self):
        assert classify_chapter(chapter("Chapter 02", 58, 148), 1423) == ("opening_theme", "medium", "length-and-position")
        assert classify_chapter(chapter("Chapter 05", 1316, 1406), 1423) == ("ending_theme", "medium", "length-and-position")

    def test_a_ninety_second_chapter_in_the_middle_is_story(self):
        assert classify_chapter(chapter("Chapter 03", 600, 690), 1423) is None

    def test_the_length_window_is_tight(self):
        assert classify_chapter(chapter("Chapter 02", 0, 80), 1423) is None
        assert classify_chapter(chapter("Chapter 02", 0, 100), 1423) is None

    def test_without_a_duration_position_cannot_be_judged(self):
        assert classify_chapter(chapter("Chapter 05", 1316, 1406), 0) is None


class TestIntervals:
    def test_intervals_are_proposed_never_approved_and_audited(self):
        chapters = [chapter("Intro", 0, 38), chapter("OP", 38, 128), chapter("Part A", 128, 700),
                    chapter("Part B", 700, 1290), chapter("ED", 1290, 1380), chapter("Next", 1380, 1410)]
        intervals, audit = chapter_intervals(chapters, 1420, run="job-1")
        assert [iv.interval_id for iv in intervals] == ["chapter-02", "chapter-05"]
        assert all(iv.is_proposed and iv.source == "chapter_marker" for iv in intervals)
        assert [iv.reason for iv in intervals] == ["opening_theme", "ending_theme"]
        assert len(audit) == 6
        assert [row["held"] for row in audit] == [False, True, False, False, True, False]
        assert audit[1]["rule"] == "named" and audit[1]["interval_id"] == "chapter-02"

    def test_a_file_without_chapters_holds_nothing(self):
        assert chapter_intervals([], 1420, run="job-1") == ([], [])


@pytest.mark.skipif(not ffmpeg_available(), reason="FFmpeg not available")
def test_probe_reads_the_chapters_ffmpeg_wrote(tmp_path):
    video = generate_synthetic_video(str(tmp_path / "plain.mkv"), [("red", 4.0), ("blue", 4.0), ("green", 4.0)])
    meta = tmp_path / "chapters.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=4000\ntitle=OP\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=4000\nEND=8000\ntitle=Part A\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=8000\nEND=12000\ntitle=ED\n", encoding="utf-8")
    chaptered = tmp_path / "chaptered.mkv"
    subprocess.run(["ffmpeg", "-y", "-i", video, "-i", str(meta), "-map_metadata", "1", "-codec", "copy", str(chaptered)],
                   capture_output=True, check=True)
    chapters, duration = probe_chapters(str(chaptered), "ffprobe")
    assert [c["title"] for c in chapters] == ["OP", "Part A", "ED"]
    assert chapters[0]["start"] == 0.0 and abs(chapters[0]["end"] - 4.0) < 0.01
    assert abs(duration - 12.0) < 0.5
    plain_chapters, _ = probe_chapters(video, "ffprobe")
    assert plain_chapters == []
