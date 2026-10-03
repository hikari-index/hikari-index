// Create (or reset) the single admin login.
//
//   node scripts/admin-password.js [user]
//
// Generates a random password, writes HIKARI_ADMIN_USER,
// HIKARI_ADMIN_PASSWORD_HASH and (if missing) HIKARI_SESSION_SECRET into
// the gallery's .env, and writes the plaintext password once to the
// gallery's admin-password.txt (both git-ignored). Read the file, then
// delete it. Nothing is printed to the terminal except where the file is.
//
// Both paths are the gallery folder whatever directory it is run from: a
// run from the workspace root once wrote a complete login into a .env
// nothing reads, beside a password file that looked current.
import { randomBytes, scryptSync } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const galleryDir = fileURLToPath(new URL("..", import.meta.url));

const user = process.argv[2] || "admin";
const password = randomBytes(18).toString("base64url");
const salt = randomBytes(16);
const hash = `scrypt:${salt.toString("hex")}:${scryptSync(password, salt, 64).toString("hex")}`;

const envPath = `${galleryDir}.env`;
const passwordPath = `${galleryDir}admin-password.txt`;
let env = existsSync(envPath) ? readFileSync(envPath, "utf-8") : "";
function setVar(name, value) {
  const line = `${name}=${value}`;
  const re = new RegExp(`^${name}=.*$`, "m");
  env = re.test(env) ? env.replace(re, line) : env.replace(/\s*$/, "") + `\n${line}\n`;
}
setVar("HIKARI_ADMIN_USER", user);
setVar("HIKARI_ADMIN_PASSWORD_HASH", hash);
if (!/^HIKARI_SESSION_SECRET=/m.test(env)) setVar("HIKARI_SESSION_SECRET", randomBytes(32).toString("hex"));
writeFileSync(envPath, env, "utf-8");
writeFileSync(passwordPath, `user: ${user}\npassword: ${password}\n`, { encoding: "utf-8", mode: 0o600 });
console.log(`admin login for "${user}" written to ${envPath}; the password is in ${passwordPath} (read it, then delete the file). Restart the dev server to pick it up.`);
