// Read-only mode (HIKARI_READ_ONLY=1): the gallery as a viewer and nothing
// else, for a copy that strangers can reach (a public demonstration, or an
// install someone puts in front of a reverse proxy). In it the gallery
// answers 404 for everything under /admin and /api, refuses every request
// that is not a GET or HEAD, reads no session, runs no import worker,
// applies no migration and writes nothing to the database, so it can run
// on a database login that may only read (ADR-0015).
import { env } from "$env/dynamic/private";
import { READ_ONLY_BUILD } from "./build-flags.js";

const on = (value) => ["1", "true", "yes"].includes(String(value ?? "").trim().toLowerCase());

export function readOnly() {
  return READ_ONLY_BUILD || on(env.HIKARI_READ_ONLY);
}

// Whether the main header carries an "Admin" link (HIKARI_ADMIN_LINK). On
// unless the setting says 0, false, no or off: a gallery on a home network
// is its owner's workbench, and the way in should be in sight. Off for an
// install the public can reach, where no page should point at the sign-in
// (ADR-0015). The link is all it governs: the sign-in gate in
// hooks.server.js stands either way, and /admin typed into the address bar
// still asks for the password. Never on in read-only mode, which has no
// admin area to link to.
const off = (value) => ["0", "false", "no", "off"].includes(String(value ?? "").trim().toLowerCase());

export function adminLink() {
  return !readOnly() && !off(env.HIKARI_ADMIN_LINK);
}

// A line of the operator's own text at the foot of every page
// (HIKARI_NOTICE): attribution for openly licensed material a public
// copy shows, or "this is a demonstration". Plain text, at most 500
// characters; web addresses in it become links (+layout.svelte). Unset,
// there is no footer.
export function notice() {
  const text = String(env.HIKARI_NOTICE ?? "").replace(/\s+/g, " ").trim();
  return text ? text.slice(0, 500) : null;
}
