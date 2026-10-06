import { fail, redirect } from "@sveltejs/kit";
import { COOKIE, checkLogin, cookieOptions, issueSession } from "$lib/server/auth.js";

export function load({ locals, url }) {
  if (locals.admin) redirect(303, safeNext(url.searchParams.get("next")));
  return { next: safeNext(url.searchParams.get("next")) };
}

// Only same-site paths; never an absolute URL from the query string. A
// backslash counts as a slash to a browser ("/\host" leaves the site), and
// control characters have no business in an address.
function safeNext(next) {
  return next && next.startsWith("/") && !next.startsWith("//") && !/[\\\u0000-\u001f\u007f]/.test(next) ? next : "/admin";
}

export const actions = {
  default: async ({ request, cookies, url }) => {
    const form = await request.formData();
    const user = String(form.get("user") ?? "");
    const password = String(form.get("password") ?? "");
    if (!checkLogin(user, password)) {
      // Same answer for a wrong name and a wrong password.
      return fail(400, { message: "That name and password do not match." });
    }
    const session = issueSession(user);
    cookies.set(COOKIE, session.value, cookieOptions(url, session.expires));
    redirect(303, safeNext(url.searchParams.get("next")));
  },
};
