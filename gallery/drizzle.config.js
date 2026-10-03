// drizzle-kit configuration: generate SQL migrations from the schema.
//   npx drizzle-kit generate      # after editing schema.js; commit the output
// The app applies ./migrations at startup; drizzle-kit never touches the
// live database here (no `push`, no `migrate` from the CLI).
import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./src/lib/server/db/schema.js",
  out: "./migrations",
});
