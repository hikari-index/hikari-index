import { fail, redirect } from "@sveltejs/kit";
import { discardPreview, discardScope, SCOPES } from "$lib/server/discard.js";

// "Done reviewing" for an episode or a season (?scope=work|season&id=...):
// shows what would be deleted, then queues the discard. Nothing is deleted
// on a GET.
export async function load({ url }) {
  const scope = url.searchParams.get("scope");
  const id = url.searchParams.get("id");
  return {
    asked: Boolean(scope && id),
    preview: scope && id ? await discardPreview(scope, id) : null,
    done: url.searchParams.get("done"),
  };
}

export const actions = {
  discard: async ({ request, locals }) => {
    const form = await request.formData();
    const scope = String(form.get("scope") ?? "");
    const target = String(form.get("id") ?? "");
    const expect = String(form.get("expect") ?? "");
    if (!SCOPES.has(scope) || !target) return fail(400, { message: "Nothing to discard." });
    if (form.get("understood") !== "yes") return fail(400, { message: "Tick the box to confirm; nothing was discarded." });
    const r = await discardScope({ scope, target, expect, requestedBy: locals.admin?.user ?? null });
    if (r.refused) return fail(409, { message: r.refused });
    redirect(303, `/admin/discard?scope=${encodeURIComponent(scope)}&id=${encodeURIComponent(target)}&done=${r.works}`);
  },
};
