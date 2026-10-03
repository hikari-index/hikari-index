import { redirect } from "@sveltejs/kit";
import { COOKIE } from "$lib/server/auth.js";

export const actions = {
  default: async ({ cookies }) => {
    cookies.delete(COOKIE, { path: "/" });
    redirect(303, "/");
  },
};
