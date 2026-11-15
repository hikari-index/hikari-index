// Plain-Node database client, no SvelteKit imports. The app wraps it in
// ./index.js with $env.
import { drizzle } from "drizzle-orm/node-postgres";
import { migrate } from "drizzle-orm/node-postgres/migrator";
import { sql } from "drizzle-orm";
import pg from "pg";
import * as schema from "./schema.js";

export function connect(url, { max = 5 } = {}) {
  if (!url) throw new Error("DATABASE_URL is not set");
  const pool = new pg.Pool({ connectionString: url, max });
  const db = drizzle(pool, { schema });
  return { db, pool, schema };
}

export async function applyMigrations(db, migrationsFolder = "migrations") {
  await migrate(db, { migrationsFolder });
  await updateVectorExtension(db);
}

// pgvector's own upgrade step (its README, "Upgrading"): after the database
// server gets a newer pgvector, each database runs ALTER EXTENSION vector
// UPDATE once. The database image moves under an install (a new pin in
// compose.yaml, a container template on a moving tag, the all-in-one
// image), and normal operation needs no hand-written SQL, so the gallery
// does it at start; it is a no-op when the database is already current.
// Not fatal: a database login that does not own the extension cannot run
// it, and the gallery works on the older extension version meanwhile.
async function updateVectorExtension(db) {
  const version = async () =>
    (await db.execute(sql`select extversion from pg_extension where extname = 'vector'`)).rows[0]?.extversion;
  try {
    const before = await version();
    if (!before) return;
    await db.execute(sql`alter extension vector update`);
    const after = await version();
    if (after !== before) console.log(`[database] pgvector updated from ${before} to ${after}`);
  } catch (error) {
    // drizzle wraps the database's own error; the reason is in its cause.
    const reason = error.cause?.message ?? error.message;
    console.warn(
      `[database] could not confirm the pgvector extension is up to date (${reason}). ` +
        "The gallery runs on the version the database has. To update it by hand, run " +
        "ALTER EXTENSION vector UPDATE in this database as the extension's owner or a superuser."
    );
  }
}
