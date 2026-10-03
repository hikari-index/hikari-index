import { adminLink } from "$lib/server/mode.js";

// What every page's header needs: whether to show the Admin link
// (HIKARI_ADMIN_LINK, mode.js). Nothing about the session: the admin
// pages take that from their own layout.
export function load() {
  return { adminLink: adminLink() };
}
