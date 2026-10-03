import { fail, redirect } from "@sveltejs/kit";
import { listRemovals, removalPreview, removeScope, SCOPES } from "$lib/server/removal.js";

// Remove an episode, a season or a whole title (?scope=work|season|franchise
// &id=...), and the record of past removals. The confirm step shows exactly
// what goes, with sizes; nothing is removed on a GET.
export async function load({ url }) {
  const scope = url.searchParams.get("scope");
  const id = url.searchParams.get("id");
  const preview = scope && id ? await removalPreview(scope, id) : null;
  return {
    asked: Boolean(scope && id),
    preview,
    done: url.searchParams.get("done"),
    removals: await listRemovals(),
  };
}

export const actions = {
  remove: async ({ request, locals }) => {
    const form = await request.formData();
    const scope = String(form.get("scope") ?? "");
    const target = String(form.get("id") ?? "");
    const reason = String(form.get("reason") ?? "").trim().slice(0, 500);
    const expect = String(form.get("expect") ?? "");
    if (!SCOPES.has(scope) || !target) return fail(400, { message: "Nothing to remove." });
    if (form.get("understood") !== "yes") return fail(400, { message: "Tick the box to confirm; nothing was removed." });
    const r = await removeScope({ scope, target, expect, reason, requestedBy: locals.admin?.user ?? null });
    if (r.refused) return fail(409, { message: r.refused });
    redirect(303, `/admin/remove?done=${r.id}`);
  },
};
