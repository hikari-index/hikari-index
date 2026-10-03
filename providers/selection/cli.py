"""Run the stratified picker (ADR-0008) over one pipeline run.

    python -m selection.cli --run-root <dir> --results-dir <dir> --out <file> [--budget N]

--run-root     the pipeline output root (prepared/<bundle>/manifest.json,
               audit/, results/completed/<run>/artifacts, surplus/)
--results-dir  the inference and annotation outputs for that run
               (embeddings/artifacts/*.embedding.json required;
               surplus-embeddings/artifacts optional; proposals.json optional)
--budget       0 = the pipeline's own published count, which on a
               derived-budget run IS the derived budget
--out          where to write the selection record

The pool is the bundle plus the surplus beside it, so the picker chooses
from every frame that cleared the quality floor. Frames without a palette
descriptor or a category label earn no palette or category bonus;
coverage, spacing and the near-twin penalty still apply to them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .stratified import RED_KNEE, W, metrics, stratified

MAX_BUDGET = 300  # the pipeline's derived-budget ceiling (frame_sample)


def only(d: Path) -> Path:
    subs = [p for p in d.iterdir() if p.is_dir()]
    if len(subs) != 1:
        raise SystemExit(f"expected exactly one directory under {d}, found {len(subs)}")
    return subs[0]


def derived_budget(run_root: Path) -> int:
    sel = json.loads((run_root / "audit" / "selection.json").read_text(encoding="utf-8"))
    cands = sel.get("candidates") or sel
    shots = {c["shot_id"] for c in cands} if isinstance(cands, list) else set()
    if not shots:
        raise SystemExit("could not read shot ids from audit/selection.json")
    return min(300, max(35, round(len(shots) * 0.15)))


def load_run(run_root: Path, results_dir: Path):
    bundle = only(run_root / "prepared")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    cands, published = {}, []
    for c in manifest["candidates"]:
        tb = c["time_base"]
        num, den = tb.split("/") if isinstance(tb, str) else tb
        cands[c["candidate_id"]] = {"ts": c["intended_pts"] * int(num) / int(den),
                                    "shot": c["shot_id"],
                                    "score": c.get("scene_score", 0.0)}
        published.append(c["candidate_id"])

    emb = {}
    for f in (results_dir / "embeddings" / "artifacts").glob("*.embedding.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        emb[d["candidate_id"]] = d["embedding"]
    if not emb:
        raise SystemExit(f"no embeddings under {results_dir / 'embeddings' / 'artifacts'}")

    surplus_dir = results_dir / "surplus-embeddings" / "artifacts"
    surplus_n = 0
    if surplus_dir.is_dir():
        audit = json.loads((run_root / "audit" / "breadth-omitted-candidates.json")
                           .read_text(encoding="utf-8"))
        rows = audit if isinstance(audit, list) else list(audit.values())[0]
        for r in rows:
            cands[r["candidate_id"]] = {"ts": r["timestamp_seconds"],
                                        "shot": r["shot_id"],
                                        "score": r.get("scene_score", 0.0)}
        for f in surplus_dir.glob("*.embedding.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            emb[d["candidate_id"]] = d["embedding"]
            surplus_n += 1

    pal = {}
    completed = run_root / "results" / "completed"
    if completed.is_dir():
        art = only(completed) / "artifacts"
        for cid in cands:
            p = art / f"{cid}.descriptor.json"
            if p.is_file():
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("hue_family") and d.get("luma"):
                    pal[cid] = (d["hue_family"]["bands"]
                                + [d["chroma"]["mean_sat_weighted"]]
                                + [d["luma"]["p05"], d["luma"]["p50"], d["luma"]["p95"]])

    cat = {}
    props_path = results_dir / "proposals.json"
    if props_path.is_file():
        props = json.loads(props_path.read_text(encoding="utf-8"))
        for p in props["proposals"]:
            lab = p["proposal"]["labels"]
            cat[p["candidate_id"]] = (lab["shot_scale"],
                                      lab["angle_composition"]["composition"],
                                      lab["people"])

    keys = [cid for cid in cands if cid in emb]
    E = np.asarray([emb[c] for c in keys], dtype=np.float32)
    E /= np.linalg.norm(E, axis=1, keepdims=True).clip(1e-9)
    P = {c: np.asarray(pal[c], dtype=np.float32) for c in pal}
    info = {"bundle_id": manifest.get("bundle_id"), "published": len(published),
            "surplus_embedded": surplus_n, "with_palette": sum(1 for k in keys if k in P),
            "with_category": sum(1 for k in keys if k in cat)}
    return keys, E, P, cat, cands, published, info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-root", type=Path, required=True)
    ap.add_argument("--results-dir", type=Path, required=True)
    ap.add_argument("--budget", type=int, default=0)
    ap.add_argument("--budget-factor", type=float, default=1.0,
                    help="scale the derived budget (the operator's fewer / balanced / more)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pin", action="append", default=[], metavar="CANDIDATE",
                    help="a frame the operator put in the set (ADR-0008 operator pin): "
                         "included on top of the budget, whatever the objective says; "
                         "one not in the pool is reported and skipped")
    args = ap.parse_args()
    if not 0.1 <= args.budget_factor <= 3.0:
        ap.error("--budget-factor must be between 0.1 and 3")

    keys, E, P, cat, cands, published, info = load_run(args.run_root, args.results_dir)
    duration = max(c["ts"] for c in cands.values()) - min(c["ts"] for c in cands.values())
    # On a derived-budget run the pipeline already applied the 0.15-per-shot
    # rule and published exactly that many, so its published count IS the
    # budget; re-deriving from audit/selection.json uses a different shot
    # denominator (54 vs 59 on one early run). Pass --budget to set it.
    budget = args.budget or min(len(published), len(keys))
    # Fewer / more scale the derived count, never past the pipeline's own
    # 300 ceiling or the pool, and never below one still.
    if args.budget_factor != 1.0:
        budget = max(1, min(round(budget * args.budget_factor), MAX_BUDGET, len(keys)))

    picks = stratified(keys, E, P, cat, cands, budget, duration)
    idx = {k: i for i, k in enumerate(keys)}
    # Operator pins ride on top of the budget: a locked frame survives
    # re-selection (ADR-0008). A pin not in the pool (its surplus frame
    # discarded, or a stale id) cannot be brought back here.
    pins = [c for c in dict.fromkeys(args.pin) if c in idx]
    pins_missing = [c for c in dict.fromkeys(args.pin) if c not in idx]
    for c in pins:
        if idx[c] not in picks:
            picks = list(picks) + [idx[c]]
    if pins_missing:
        print(f"pins not in the pool, skipped: {', '.join(pins_missing)}")
    row = {"stratified": metrics(picks, keys, E, P, cat, cands, duration)}
    # The pipeline's own published set is a fair comparison only when it is
    # the same size as the budget (a derived-budget run). Truncating a
    # 200-frame annotation-budget set to the first N would just be the
    # first N minutes in time order, which is not a selector.
    pipeline_set = [idx[c] for c in published if c in idx]
    if len(pipeline_set) == budget:
        row["pipeline-published"] = metrics(pipeline_set, keys, E, P, cat, cands, duration)
    else:
        print(f"(pipeline published {len(pipeline_set)} != budget {budget}: "
              f"no baseline row; run at the derived budget to compare)")
    print(f"{info['bundle_id']}: pool {len(keys)} (published {info['published']}, "
          f"surplus embedded {info['surplus_embedded']}), palette on {info['with_palette']}, "
          f"categories on {info['with_category']}, budget {budget}, ~{duration/60:.0f} min")
    hdr = ["system", "embCov", "palDist", "cats", "maxGap", "closestPair"]
    print(f"{hdr[0]:20s}{hdr[1]:>8s}{hdr[2]:>9s}{hdr[3]:>10s}{hdr[4]:>9s}{hdr[5]:>12s}")
    for name, m in row.items():
        print(f"{name:20s}{m['embedding_coverage']:8.3f}"
              f"{(m['palette_mean_dist_to_selected'] or 0):9.3f}"
              f"{m['category_coverage']:>10s}"
              f"{(m['max_gap_s'] or 0):9.1f}"
              f"{m['closest_pair_sim']:12.3f}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "selector": "stratified-picker-adr-0008",
        "objective_source": "providers/selection/stratified.py",
        "weights": W, "redundancy_knee": RED_KNEE,
        "run_root": str(args.run_root), "results_dir": str(args.results_dir),
        "pool": len(keys), "budget": budget, "inputs": info,
        "pinned": pins, "pins_missing": pins_missing,
        "selected": [keys[i] for i in picks],
        "metrics": row,
    }, indent=1), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
