import { checkedStills, ENOUGH, runsOf, summarize } from "$lib/server/accuracy.js";

// The label report (accuracy.js): review agreement per label, source and
// value, over the stills whose labels were marked checked.
export async function load() {
  const checks = await checkedStills();
  return { total: checks.length, enough: ENOUGH, runs: runsOf(checks), families: summarize(checks) };
}
