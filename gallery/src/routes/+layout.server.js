import { adminLink, notice } from "$lib/server/mode.js";

// What every page's frame needs: whether to show the Admin link
// (HIKARI_ADMIN_LINK) and the operator's footer line (HIKARI_NOTICE), both
// in mode.js. Nothing about the session: the admin pages take that from
// their own layout.
export function load() {
  return { adminLink: adminLink(), notice: notice() };
}
