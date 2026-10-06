// One admin account, framework-plain: a scrypt password hash and an HMAC-
// signed session cookie, both from Node's crypto. No user table yet: the
// research asks for named admin accounts and roles before anything is
// exposed beyond the LAN; this is the single-owner version that keeps the
// review and editing forms off the public side today.
//
// Environment (gallery/.env, git-ignored; scripts/admin-password.js writes it):
//   HIKARI_ADMIN_USER            the login name
//   HIKARI_ADMIN_PASSWORD_HASH   scrypt:<salt hex>:<hash hex>  (colons: a "$" in .env is
//                                expanded as a variable by the dev server)
//   HIKARI_SESSION_SECRET        32+ random bytes, hex
import { createHmac, randomBytes, scryptSync, timingSafeEqual } from "node:crypto";
import { env } from "$env/dynamic/private";

export const COOKIE = "hikari_admin";
const SESSION_DAYS = 7;

export function hashPassword(password, salt = randomBytes(16)) {
  const hash = scryptSync(password, salt, 64);
  return `scrypt:${salt.toString("hex")}:${hash.toString("hex")}`;
}

export function verifyPassword(password, stored) {
  const [scheme, saltHex, hashHex] = String(stored ?? "").split(":");
  if (scheme !== "scrypt" || !saltHex || !hashHex) return false;
  const expected = Buffer.from(hashHex, "hex");
  const actual = scryptSync(password, Buffer.from(saltHex, "hex"), expected.length);
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

function sign(payload, secret) {
  return createHmac("sha256", secret).update(payload).digest("hex");
}

export function issueSession(user) {
  const secret = env.HIKARI_SESSION_SECRET;
  if (!secret) throw new Error("HIKARI_SESSION_SECRET is not set");
  const expires = Date.now() + SESSION_DAYS * 86400000;
  const payload = `${user}|${expires}`;
  return { value: `${payload}|${sign(payload, secret)}`, expires: new Date(expires) };
}

export function readSession(value) {
  const secret = env.HIKARI_SESSION_SECRET;
  if (!secret || !value) return null;
  const [user, expires, mac] = String(value).split("|");
  if (!user || !expires || !mac) return null;
  const expected = sign(`${user}|${expires}`, secret);
  if (expected.length !== mac.length || !timingSafeEqual(Buffer.from(expected), Buffer.from(mac))) return null;
  if (Number(expires) < Date.now()) return null;
  return { user };
}

export function checkLogin(user, password) {
  const wantUser = env.HIKARI_ADMIN_USER;
  const wantHash = env.HIKARI_ADMIN_PASSWORD_HASH;
  if (!wantUser || !wantHash) return false;
  // Compare the user name in constant time too; it is cheap.
  const a = Buffer.from(String(user)), b = Buffer.from(wantUser);
  const sameUser = a.length === b.length && timingSafeEqual(a, b);
  return sameUser && verifyPassword(password, wantHash);
}

// Secure when the gallery is reached over HTTPS (ORIGIN, or the proxy's
// protocol header). Sign-out passes the same options: over plain HTTP a
// browser ignores a Secure delete, which left the owner signed in.
export const cookieOptions = (url, expires) => ({
  path: "/",
  httpOnly: true,
  sameSite: "lax",
  secure: url.protocol === "https:",
  ...(expires ? { expires } : {}),
});
