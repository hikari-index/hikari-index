// Plain-Node database client, no SvelteKit imports. The app wraps it in
// ./index.js with $env.
import { drizzle } from "drizzle-orm/node-postgres";
import { migrate } from "drizzle-orm/node-postgres/migrator";
import pg from "pg";
import * as schema from "./schema.js";

export function connect(url, { max = 5 } = {}) {
  if (!url) throw new Error("DATABASE_URL is not set");
  const pool = new pg.Pool({ connectionString: url, max });
  const db = drizzle(pool, { schema });
  return { db, pool, schema };
}

export function applyMigrations(db, migrationsFolder = "migrations") {
  return migrate(db, { migrationsFolder });
}
