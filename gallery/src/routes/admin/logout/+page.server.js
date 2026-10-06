import { redirect } from "@sveltejs/kit";
import { COOKIE, cookieOptions } from "$lib/server/auth.js";

export const actions = {
  default: async ({ cookies, url }) => {
    cookies.delete(COOKIE, cookieOptions(url));
    redirect(303, "/");
  },
};
