import { error, fail, redirect } from "@sveltejs/kit";
import { facetChoices, setCorrections, setFlag, setReview, stillById, workMeta, workStills } from "$lib/server/catalog.js";

// The still editor: fix a label the model got wrong, add or remove tags,
// leave a note. Corrections sit beside the machine's values; "as proposed"
// removes a correction, "none" clears the machine's value.
export async function load({ params }) {
  const id = `${params.work}/${params.candidate}`;
  const still = await stillById(id, { admin: true });
  if (!still) error(404, "no such still");
  // Walk the whole episode from here: every selected still in time order,
  // hidden and culled ones included, so nothing is skipped by accident.
  const all = await workStills(params.work, { includeHidden: true, admin: true });
  const index = all.findIndex((x) => x.id === id);
  const prev = index > 0 ? all[index - 1] : null;
  const next = index >= 0 && index < all.length - 1 ? all[index + 1] : null;
  const nextUnreviewed = all.slice(index + 1).find((x) => x.review === "unreviewed" && !x.excluded) ?? null;
  const unreviewedLeft = all.filter((x) => x.review === "unreviewed" && !x.excluded).length;
  return {
    still,
    work: await workMeta(params.work),
    choices: await facetChoices(),
    walk: { index: index + 1, total: all.length, prev, next, nextUnreviewed, unreviewedLeft },
  };
}

const TAG = /^[a-z0-9][a-z0-9 _:'()!\-]{0,40}$/;

export const actions = {
  save: async ({ request, params }) => {
    const id = `${params.work}/${params.candidate}`;
    const still = await stillById(id, { admin: true });
    if (!still) error(404, "no such still");
    const form = await request.formData();
    const choices = await facetChoices();
    // Handed back with a refusal so the form can show what was typed.
    const values = () => ({
      ...Object.fromEntries([...form.entries()].filter(([k]) => k !== "tag").map(([k, x]) => [k, String(x)])),
      tags: form.getAll("tag").map(String),
    });

    const facetsHuman = {};
    for (const family of Object.keys(choices)) {
      const raw = String(form.get(`facet:${family}`) ?? "__proposed");
      const typed = String(form.get(`facet:${family}:new`) ?? "").trim().toLowerCase().replaceAll(" ", "-");
      const value = typed || raw;
      if (value === "__proposed") continue; // no correction
      if (value === "__none") facetsHuman[family] = null;
      else if (/^[a-z0-9][a-z0-9-]{0,30}$/.test(value)) facetsHuman[family] = value;
      else return fail(400, { message: `"${value}" is not a usable value for ${family}`, values: values() });
    }
    const machineTags = new Set(still.machine.tags);
    const keep = new Set(form.getAll("tag").map(String));
    const remove = [...machineTags].filter((t) => !keep.has(t));
    const add = String(form.get("tags:add") ?? "")
      .split(",")
      .map((t) => t.trim().toLowerCase())
      .filter((t) => t && !machineTags.has(t));
    for (const t of add) if (!TAG.test(t)) return fail(400, { message: `"${t}" is not a usable tag`, values: values() });
    const previouslyAdded = (still.human.tags.add ?? []).filter((t) => keep.has(t));
    const tagsHuman = { add: [...new Set([...previouslyAdded, ...add])], remove };
    const note = String(form.get("note") ?? "").trim() || null;

    // A checked save certifies the labels the page showed; refuse it, before
    // anything is written, if a re-run has changed them since.
    const checked = form.get("labels_checked") === "on";
    const seen = JSON.stringify(Object.entries(still.machine.facets).sort());
    if (checked && String(form.get("labels_seen") ?? "") !== seen) {
      // The tick comes back off: it was given on labels no longer shown.
      return fail(409, { message: "The machine's labels changed since this page was opened (the work was re-run). Nothing was saved. Look at the labels again, then tick \"Labels checked\" and save.", values: { ...values(), labels_checked: "" } });
    }
    // "Labels checked": every label was looked at, so what was left as
    // proposed is agreement. Saved as a snapshot of what was judged (the
    // accuracy report); unticked, the mark comes off.
    const labelCheck = checked
      ? { at: new Date().toISOString(), run: still.machine.run, machine: still.machine.facets, sources: still.machine.sources, human: facetsHuman }
      : still.labelCheck ? null : undefined;
    const saved = await setCorrections(id, {
      facetsHuman: Object.keys(facetsHuman).length ? facetsHuman : null,
      tagsHuman: tagsHuman.add.length || tagsHuman.remove.length ? tagsHuman : null,
      note,
      labelCheck,
    });
    if (!saved) error(404, "This still is gone: a re-run removed it while you were editing. Nothing was saved.");
    const state = String(form.get("state") ?? "");
    if (["kept", "culled", "unreviewed"].includes(state) && state !== still.review) await setReview(id, state, note);
    let lockRefused = false;
    for (const flag of ["locked", "excluded"]) {
      const want = form.get(flag) === "on";
      if (want !== still[flag] && !(await setFlag(id, flag, want))) lockRefused = true;
    }
    if (lockRefused) return fail(409, { message: "Not locked: this frame has no web image yet and this work's extra frames were discarded, so a re-run could never bring it in. Everything else you changed was saved.", values: { ...values(), locked: false } });
    const next = String(form.get("next") ?? "");
    if (next.startsWith("/admin/")) redirect(303, next);
    return { saved: true };
  },
};
