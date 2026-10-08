"""Scoring a run against the owner's checked stills."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROVIDER_ROOT = Path(__file__).resolve().parents[2] / "providers"
sys.path.insert(0, str(PROVIDER_ROOT))

from annotation.score import UNANSWERED, load_run, main, score, truth


def _run(tmp_path, scale_by_candidate, lane="face-occupancy"):
    proposals = []
    for cid, scale in scale_by_candidate.items():
        proposals.append({
            "candidate_id": cid,
            "provenance": {"shot_scale_lane": lane,
                           "fields": {"shot_scale": {"source": lane, "score": 0.5}}},
            "proposal": {"labels": {"shot_scale": scale, "people": "one"}},
        })
    folder = tmp_path / "analyze"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "proposals.json").write_text(json.dumps(
        {"run_ref": "run-film-a", "proposals": proposals}), encoding="utf-8")
    return folder


def test_truth_is_the_owner_value_else_the_reviewed_one():
    check = {"machine": {"shot_scale": "medium", "people": "one"},
             "human": {"shot_scale": "close-up", "people": None}}
    assert truth(check, "shot_scale") == "close-up"
    assert truth(check, "people") is None  # cleared: nothing applies
    assert truth({"machine": {"time": "day"}, "human": {}}, "time") == "day"
    assert truth({"machine": {}, "human": {}}, "time") is None


def test_candidate_and_reviewed_are_scored_on_the_same_stills(tmp_path):
    work, run = load_run(_run(tmp_path, {"cand-0001": "close-up", "cand-0002": "abstain",
                                         "cand-0003": "wide"}))
    assert work == "film-a"
    checks = [
        # reviewed said medium, owner corrected to close-up: candidate right.
        {"work": "film-a", "candidate": "cand-0001",
         "machine": {"shot_scale": "medium"}, "human": {"shot_scale": "close-up"}},
        # reviewed abstained, owner filled wide: candidate abstains, a miss.
        {"work": "film-a", "candidate": "cand-0002", "machine": {}, "human": {"shot_scale": "wide"}},
        # left as proposed: wide is the truth, candidate agrees.
        {"work": "film-a", "candidate": "cand-0003", "machine": {"shot_scale": "wide"}, "human": {}},
        {"work": "film-b", "candidate": "cand-0001", "machine": {}, "human": {}},
    ]
    result = score(checks, {work: run})
    assert (result["matched"], result["unmatched"]) == (3, 1)
    scale = result["families"]["shot_scale"]
    assert scale["reviewed"] == {"answered": 2, "right": 1, "missed": 1, "truths": 3}
    assert scale["candidate"] == {"answered": 2, "right": 2, "missed": 1, "truths": 3}
    assert scale["by_value"]["face-occupancy / close-up"]["right"] == 1


def test_an_unanswered_suggestion_is_left_out(tmp_path):
    # The gallery holds a head's shot size as a suggestion (#41): accepted,
    # it is the owner's value; never answered, it is no judgment at all.
    work, run = load_run(_run(tmp_path, {"cand-0001": "wide", "cand-0002": "wide"}, lane="head-height"))
    suggested = {"shot_scale": {"value": "wide", "score": 0.3, "source": "head-height"}}
    checks = [
        {"work": work, "candidate": "cand-0001", "machine": {}, "human": {}, "suggested": suggested},
        {"work": work, "candidate": "cand-0002", "machine": {}, "human": {"shot_scale": "wide"}, "suggested": suggested},
    ]
    assert truth(checks[0], "shot_scale") is UNANSWERED
    assert truth(checks[1], "shot_scale") == "wide"
    scale = score(checks, {work: run})["families"]["shot_scale"]
    assert scale["candidate"] == {"answered": 1, "right": 1, "missed": 0, "truths": 1}
    assert scale["reviewed"] == {"answered": 0, "right": 0, "missed": 1, "truths": 1}


def test_the_cli_refuses_a_file_that_is_not_a_download(tmp_path, capsys):
    folder = _run(tmp_path, {"cand-0001": "wide"})
    checks = tmp_path / "checks.json"
    checks.write_text(json.dumps({"checks": []}), encoding="utf-8")
    try:
        main(["--checks", str(checks), str(folder)])
    except ValueError as error:
        assert "label-checks download" in str(error)
    else:
        raise AssertionError("accepted a file without the format marker")
    checks.write_text(json.dumps({"format": "hikari-label-checks-1", "checks": [
        {"work": "film-a", "candidate": "cand-0001", "machine": {"shot_scale": "wide"}, "human": {}}]}),
        encoding="utf-8")
    assert main(["--checks", str(checks), str(folder)]) == 0
    assert "1 checked stills scored" in capsys.readouterr().out
