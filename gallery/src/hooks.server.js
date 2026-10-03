import { error, redirect } from "@sveltejs/kit";
import { ensureMigrated } from "$lib/server/db/index.js";
import { COOKIE, readSession } from "$lib/server/auth.js";
import { startGalleryWorker } from "$lib/server/gallery-worker.js";
import { readOnly } from "$lib/server/mode.js";

// The import stage runs in this process (off without HIKARI_RUNS, and
// never in read-only mode).
if (!readOnly()) startGalleryWorker();

const under = (path, root) => path === root || path.startsWith(`${root}/`);

// Whether a request belongs to an area (/admin, /api) is decided by the
// route SvelteKit matched, not by the address as it was typed. The router
// decodes percent-escapes before it matches ("/%61dmin" is /admin), so a
// check on the raw path alone let such an address past the sign-in, pages
// and actions both (found 2026-10-02; every build before it has this).
// The raw path is checked as well, so an address under the area that
// matches no route is treated the same way.
const inArea = (event, root) => under(event.route.id ?? "", root) || under(event.url.pathname, root);

// Every request waits for the schema to be current (an already-resolved
// promise after the first). Everything under /admin needs a valid session
// except the login page itself; the public routes carry no form actions.
//
// In read-only mode (mode.js) there is no admin area and no worker API at
// all: both answer 404 whether or not their routes were built into this
// image, nothing but GET and HEAD is accepted, no session is read, and
// the database is only read, so no migration is applied (give it a
// database that a normal gallery of this version has already brought up
// to date).
export async function handle({ event, resolve }) {
  if (readOnly()) {
    if (inArea(event, "/admin") || inArea(event, "/api")) error(404, "Not Found");
    if (event.request.method !== "GET" && event.request.method !== "HEAD") {
      return new Response("Method Not Allowed", { status: 405, headers: { allow: "GET, HEAD" } });
    }
    event.locals.admin = null;
    return resolve(event);
  }
  await ensureMigrated();
  event.locals.admin = readSession(event.cookies.get(COOKIE));
  if (inArea(event, "/admin") && event.route.id !== "/admin/login" && !event.locals.admin) {
    redirect(303, `/admin/login?next=${encodeURIComponent(event.url.pathname + event.url.search)}`);
  }
  return resolve(event);
}
