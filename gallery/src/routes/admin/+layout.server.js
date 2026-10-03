import { redirect } from "@sveltejs/kit";

// The sign-in gate is hooks.server.js: it runs before any load, action or
// endpoint. This is only a second check on the way through this layout
// (SvelteKit may start a page's own load beside it, and a form action
// never comes through here), so nothing may rely on it.
export function load({ locals, route, url }) {
  if (!locals.admin && route.id !== "/admin/login") {
    redirect(303, `/admin/login?next=${encodeURIComponent(url.pathname + url.search)}`);
  }
  return { admin: locals.admin ?? null };
}
