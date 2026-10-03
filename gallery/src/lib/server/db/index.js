// Database access for the app. The app owns the schema: the first request
// applies any migration under ./migrations the database has not seen
// (drizzle keeps its own ledger table), so routine operation never needs a
// shell or hand-written SQL.
import { env } from "$env/dynamic/private";
import { applyMigrations, connect } from "./client.js";

let conn;
let migrated;

export function db() {
  if (!conn) conn = connect(env.DATABASE_URL);
  return conn.db;
}

// Idempotent; called from the server hook on every request. The first call
// does the work, the rest await the same promise; a failure lets the next
// request retry instead of poisoning the app.
export function ensureMigrated() {
  if (!migrated) {
    migrated = applyMigrations(db()).catch((error) => {
      migrated = undefined;
      throw error;
    });
  }
  return migrated;
}

export * as schema from "./schema.js";
