import { fail, redirect } from "@sveltejs/kit";
import { cleanPath, queueListing, recentListings, sourceRoots } from "$lib/server/local.js";
import { shokoConfigured } from "$lib/server/shoko.js";

// Add a folder of episodes without Shoko (ADR-0014 amendment): the
// operator names a folder inside a source root; the worker lists its video
// files; the next page shows them for the operator to confirm. Nothing
// runs until that confirm, and nothing but the one named folder is read.

export async function load() {
  return {
    roots: await sourceRoots(),
    shoko: shokoConfigured(),
    recent: await recentListings(),
  };
}

export const actions = {
  default: async ({ request, locals }) => {
    const form = await request.formData();
    const values = { root: String(form.get("root") ?? "").trim(), path: String(form.get("path") ?? "").trim() };
    const problems = {};
    const roots = await sourceRoots();
    if (!roots.names.length) return fail(409, { values, problems, message: "Nothing was asked: no worker has reported a source folder yet." });
    if (!roots.names.includes(values.root)) problems.root = "Choose the source folder the files are in.";
    // An empty path is the source folder itself (it may be the season).
    const path = values.path ? cleanPath(values.path) : "";
    if (path == null) problems.path = "Give the folder's path inside the source folder, without “..” parts.";
    if (Object.keys(problems).length) return fail(400, { values, problems, message: "Nothing was asked; fix the fields marked below." });
    const id = await queueListing({ root: values.root, path, requestedBy: locals.admin?.user ?? null });
    redirect(303, `/admin/onboard/folder/${id}`);
  },
};
