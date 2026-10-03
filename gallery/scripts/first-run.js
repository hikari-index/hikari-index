// Makes the secrets a new install needs and prints them as lines for the
// repository-root `.env` (compose.yaml reads it):
//
//   docker compose run --rm --no-deps gallery node scripts/first-run.js [user]
//
// It writes nothing anywhere. The admin password is shown once, here; only
// its hash goes into `.env`. To change the password later, run it again
// and replace the three HIKARI_ADMIN_* / HIKARI_SESSION_SECRET lines (keep
// the database password: the database was created with it).
import { randomBytes, scryptSync } from "node:crypto";

const user = process.argv[2] || "admin";
const password = randomBytes(18).toString("base64url");
const salt = randomBytes(16);
// Colons, never "$": a "$" in an env file is read as a variable.
const hash = `scrypt:${salt.toString("hex")}:${scryptSync(password, salt, 64).toString("hex")}`;
const token = randomBytes(32).toString("hex");

console.log(`
Paste these lines at the end of .env:

HIKARI_DB_PASSWORD=${randomBytes(24).toString("hex")}
HIKARI_ADMIN_USER=${user}
HIKARI_ADMIN_PASSWORD_HASH=${hash}
HIKARI_SESSION_SECRET=${randomBytes(32).toString("hex")}
HIKARI_ANALYZE_TOKEN=${token}
HIKARI_WORKER_TOKENS=analyze:${token}

Your admin sign-in (shown once; it is not stored anywhere):

  user:     ${user}
  password: ${password}
`);
