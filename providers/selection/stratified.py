"""The production selector (ADR-0008): the stratified picker.

The objective below is the one the two blinded A/B rounds judged, copied
verbatim from the offline optimizer used in that comparison (its weights, its near-twin
knee, `stratified` and `metrics`); only its test-data loaders were left
behind. Changing any number here changes what publishes, and ADR-0008
records them as constants, not a tuning surface.
"""

from collections import defaultdict

import numpy as np

W = {"cov": 1.0, "pal": 0.35, "cat": 0.25, "time": 0.4, "red": 6.0}
# Redundancy: penalty starts when a candidate is >90% similar to an existing
# pick and grows steeply, sized so a near-twin (0.95+) always loses more than
# the category bonus (0.25) could buy it.
RED_KNEE = 0.90


def metrics(sel_idx, keys, E, P, cat, cands, duration):
    S = E[sel_idx]
    cover = float((E @ S.T).max(axis=1).mean())
    sel_keys = [keys[i] for i in sel_idx]
    pal_sel = [P[k] for k in sel_keys if k in P]
    pal_cov = None
    if pal_sel:
        pool_pal = [P[k] for k in keys if k in P]
        A = np.stack(pool_pal)
        B = np.stack(pal_sel)
        d = np.linalg.norm(A[:, None, :] - B[None, :, :], axis=2).min(axis=1)
        pal_cov = float(d.mean())
    cats_pool = {cat.get(k) for k in keys if cat.get(k)}
    cats_sel = {cat.get(k) for k in sel_keys if cat.get(k)}
    ts = sorted(cands[k]["ts"] for k in sel_keys)
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    sims = E[sel_idx] @ E[sel_idx].T
    np.fill_diagonal(sims, -1)
    return {
        "embedding_coverage": round(cover, 4),
        "palette_mean_dist_to_selected": round(pal_cov, 4) if pal_cov is not None else None,
        "category_coverage": f"{len(cats_sel)}/{len(cats_pool)}",
        "max_gap_s": round(max(gaps), 1) if gaps else None,
        "closest_pair_sim": round(float(sims.max()), 4),
    }


def stratified(keys, E, P, cat, cands, budget, duration):
    """Round-2 objective from the blinded A/B: even spacing FIRST (the
    preference data's verdict), variety as the within-cell tiebreaker.

    Runtime is cut into `budget` equal cells; each non-empty cell contributes
    one pick, chosen by the variety terms (coverage gain, palette novelty,
    new category, near-twin penalty). Budget left over from empty cells goes
    to the best remaining frames anywhere, same objective. Spacing is
    guaranteed by construction rather than bought with a weight."""
    n = len(keys)
    sims_all = (E @ E.T).astype(np.float32)
    t0 = min(c["ts"] for c in cands.values())
    cell_w = duration / budget
    cells = defaultdict(list)
    for j, k in enumerate(keys):
        c = int(min(budget - 1, (cands[k]["ts"] - t0) / cell_w))
        cells[c].append(j)

    best_sim = np.zeros(n, dtype=np.float32)
    selected, pal_sel, seen_cats = [], [], set()

    def variety_score(j):
        k = keys[j]
        s = float(np.maximum(sims_all[j] - best_sim, 0).sum()) / n
        if k in P:
            if pal_sel:
                B = np.stack(pal_sel)
                s += W["pal"] * float(np.linalg.norm(B - P[k][None, :], axis=1).min())
            else:
                s += W["pal"] * 0.5
        c = cat.get(k)
        if c and c not in seen_cats:
            s += W["cat"]
        if selected:
            twin = float(sims_all[j, selected].max())
            s -= W["red"] * max(0.0, twin - RED_KNEE)
        return s

    def take(j):
        selected.append(j)
        best_sim[:] = np.maximum(best_sim, sims_all[j])
        k = keys[j]
        if k in P:
            pal_sel.append(P[k])
        c = cat.get(k)
        if c:
            seen_cats.add(c)

    for c in sorted(cells):
        if len(selected) >= budget:
            break
        take(max(cells[c], key=variety_score))
    leftovers = budget - len(selected)
    if leftovers > 0:
        remaining = [j for j in range(n) if j not in set(selected)]
        for _ in range(min(leftovers, len(remaining))):
            j = max((x for x in remaining if x not in set(selected)),
                    key=variety_score)
            take(j)
    return selected
