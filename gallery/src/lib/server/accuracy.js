// The label accuracy report: how often the owner's review agreed with the
// machine's labels, per label, per source and per value, over the stills
// whose labels were marked checked (stills.label_check). It is the
// yardstick for a new model or a changed cut-off: judge the change on the
// reviewed stills before it touches the library. The export carries the
// same snapshots for scoring a candidate run offline
// (providers/annotation/score.py).
import { sql } from "drizzle-orm";
import { db } from "./db/index.js";

// The labels a still carries as facets, in the order the report shows them.
export const REPORT_FAMILIES = ["shot_scale", "composition", "people", "setting", "time", "weather", "lighting", "color_bias", "saturation", "angle"];

// Below this many reviewed stills a share is shown but not trusted: the
// face cut-offs were set from 53 reviewed frames, and about 20 per source
// per value is the least that re-tunes one.
export const ENOUGH = 20;

// One label on one checked still. The truth is the owner's value where
// they set one (null: nothing applies) and the machine's where they left
// it as proposed, which a checked still makes agreement.
export function outcome(check, family) {
  const machine = check.machine?.[family] ?? null;
  const human = check.human ?? {};
  const truth = family in human ? human[family] ?? null : machine;
  let kind;
  if (machine && truth === machine) kind = "agreed";
  else if (machine && truth) kind = "corrected";
  else if (machine) kind = "cleared";
  else if (truth) kind = "missed";
  else kind = "none";
  return { machine, truth, kind, source: check.sources?.[family]?.s ?? null };
}

const share = (n, d) => (d ? n / d : null);

// checks: label_check snapshots. Per family: the counts, then per source
// and per machine value how many proposals review agreed with, and what
// the corrected ones were corrected to.
export function summarize(checks) {
  const families = [];
  for (const family of REPORT_FAMILIES) {
    const f = { family, checked: 0, proposed: 0, agreed: 0, corrected: 0, cleared: 0, missed: 0, none: 0, sources: new Map(), values: new Map(), filled: new Map() };
    for (const check of checks) {
      const o = outcome(check, family);
      f.checked += 1;
      f[o.kind] += 1;
      if (o.kind === "missed") f.filled.set(o.truth, (f.filled.get(o.truth) ?? 0) + 1);
      if (!o.machine) continue;
      f.proposed += 1;
      const src = o.source ?? "not recorded";
      const s = f.sources.get(src) ?? { source: src, n: 0, agreed: 0 };
      s.n += 1;
      if (o.kind === "agreed") s.agreed += 1;
      f.sources.set(src, s);
      const key = `${src}\u0000${o.machine}`;
      const v = f.values.get(key) ?? { source: src, value: o.machine, n: 0, agreed: 0, to: new Map() };
      v.n += 1;
      if (o.kind === "agreed") v.agreed += 1;
      else v.to.set(o.truth ?? "none", (v.to.get(o.truth ?? "none") ?? 0) + 1);
      f.values.set(key, v);
    }
    const byCount = (a, b) => b.n - a.n || String(a.value ?? a.source).localeCompare(String(b.value ?? b.source));
    families.push({
      family,
      checked: f.checked,
      proposed: f.proposed,
      agreed: f.agreed,
      corrected: f.corrected,
      cleared: f.cleared,
      missed: f.missed,
      none: f.none,
      agreement: share(f.agreed, f.proposed),
      sources: [...f.sources.values()].map((s) => ({ ...s, agreement: share(s.agreed, s.n) })).sort(byCount),
      values: [...f.values.values()]
        .map((v) => ({ ...v, agreement: share(v.agreed, v.n), to: [...v.to].sort((a, b) => b[1] - a[1]).map(([value, n]) => ({ value, n })) }))
        .sort((a, b) => a.source.localeCompare(b.source) || byCount(a, b)),
      filled: [...f.filled].sort((a, b) => b[1] - a[1]).map(([value, n]) => ({ value, n })),
    });
  }
  return families;
}

// The checked stills, as the report and the export read them.
export async function checkedStills() {
  const rows = (await db().execute(sql`select id, work_id, candidate_id, review_state, label_check
    from stills where label_check is not null order by work_id, ts_seconds nulls last, candidate_id`)).rows;
  return rows.map((r) => ({ id: r.id, work: r.work_id, candidate: r.candidate_id, review: r.review_state, ...r.label_check }));
}

// The runs the checks were made against, newest label versions first, so
// the page can say what it is reporting on.
export function runsOf(checks) {
  const runs = new Map();
  for (const c of checks) {
    const r = c.run ?? {};
    const key = JSON.stringify([r.taxonomy ?? null, r.allowlist ?? null, r.fusion ?? null]);
    const entry = runs.get(key) ?? { taxonomy: r.taxonomy ?? null, allowlist: r.allowlist ?? null, fusion: r.fusion ?? null, n: 0 };
    entry.n += 1;
    runs.set(key, entry);
  }
  return [...runs.values()].sort((a, b) => b.n - a.n);
}
