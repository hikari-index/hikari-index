// Read-only Shoko client for the Onboard picker, plain Node (no SvelteKit
// imports) so a script can use it too; ./shoko.js wraps it with $env.
//
// Shoko is the metadata authority and this project never changes it. So:
// GET only, and the method is fixed here rather than a parameter (Shoko's
// DELETE /File/{id} defaults to removing the file from disk); only the
// paths below can be reached, the same slice tools/shoko_lookup.py uses;
// the key goes in the `apikey` header and nowhere else, never logged, never
// returned to a page. The key must belong to a non-admin user.
import { readFileSync } from "node:fs";

const TIMEOUT_MS = 20000;
const MAX_BYTES = 8 * 1024 * 1024;

const ALLOWED = new Set([
  "/api/v3/User/Current",
  "/api/v3/Series/Search",
  "/api/v3/Series/{series}",
  "/api/v3/Series/{series}/Episode",
  "/api/v3/Episode/{episode}",
  "/api/v3/Episode/{episode}/File",
  "/api/v3/File/{file}",
  // Franchise hierarchy (a Shoko Group is the franchise, its Series the
  // seasons), read by the import stage so a new work lands in the Library.
  "/api/v3/Series/{series}/Group",
  "/api/v3/Group/{group}/Series",
]);

export class ShokoError extends Error {}

export function readKey({ key, keyFile } = {}) {
  if (key) return key.trim();
  if (keyFile) {
    try {
      return readFileSync(keyFile, "utf8").trim();
    } catch {
      throw new ShokoError("the Shoko key file cannot be read");
    }
  }
  throw new ShokoError("no Shoko key: set HIKARI_SHOKO_API_KEY_FILE");
}

export function client({ baseUrl, key }) {
  if (!baseUrl) throw new ShokoError("no Shoko address: set HIKARI_SHOKO_BASE_URL");
  const base = baseUrl.replace(/\/$/, "");

  async function get(template, fields = {}, params = {}) {
    if (!ALLOWED.has(template)) throw new ShokoError(`${template} is not in the read-only allowlist`);
    const path = template.replace(/\{(\w+)\}/g, (_, name) => {
      const v = fields[name];
      if (!Number.isInteger(v) || v <= 0) throw new ShokoError(`bad ${name} id`);
      return String(v);
    });
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      for (const one of Array.isArray(v) ? v : [v]) qs.append(k, String(one));
    }
    const url = `${base}${path}${qs.size ? `?${qs}` : ""}`;
    let r;
    try {
      r = await fetch(url, {
        method: "GET",
        headers: { accept: "application/json", apikey: key },
        redirect: "error",
        signal: AbortSignal.timeout(TIMEOUT_MS),
      });
    } catch (error) {
      throw new ShokoError(`cannot reach Shoko (${error.name === "TimeoutError" ? "timed out" : error.cause?.code || error.message})`);
    }
    if (r.status !== 200) throw new ShokoError(`${path} returned HTTP ${r.status}`);
    const body = await r.arrayBuffer();
    if (body.byteLength > MAX_BYTES) throw new ShokoError(`${path} answered more than ${MAX_BYTES} bytes`);
    return JSON.parse(new TextDecoder().decode(body));
  }

  // A paged list endpoint, followed to the end (Shoko reports Total).
  async function all(template, fields, params) {
    const found = [];
    for (let page = 1; page < 50; page++) {
      const payload = await get(template, fields, { ...params, page, pageSize: 100 });
      const items = Array.isArray(payload) ? payload : payload.List || [];
      found.push(...items);
      if (Array.isArray(payload) || !items.length || found.length >= (payload.Total ?? 0)) break;
    }
    return found;
  }

  return {
    // Refuses an admin key: non-admin is not proof every endpoint is
    // read-only, but reading with an admin key is a mistake worth failing on.
    async whoami() {
      const user = await get("/api/v3/User/Current");
      if (user.IsAdmin) throw new ShokoError("the Shoko key belongs to an admin user; use the non-admin service key");
      return { user: user.Username, isAdmin: Boolean(user.IsAdmin) };
    },
    searchSeries: (query, limit = 25) => get("/api/v3/Series/Search", {}, { query, limit }),
    series: (series) => get("/api/v3/Series/{series}", { series }, { includeDataFrom: "AniDB" }),
    // Episode number and type live behind the AniDB expansion; without it
    // they read as null, which looks like the unmapped case.
    episodes: (series) =>
      all("/api/v3/Series/{series}/Episode", { series }, { includeDataFrom: "AniDB", includeFiles: true, includeMediaInfo: true, includeXRefs: true }),
    episode: (episode) => get("/api/v3/Episode/{episode}", { episode }, { includeDataFrom: "AniDB" }),
    file: (file) => get("/api/v3/File/{file}", { file }, { include: ["XRefs", "MediaInfo"] }),
    // The franchise a series belongs to and every series in it (the shape
    // the import's catalogue sync reads).
    async catalogue(series) {
      const group = await get("/api/v3/Series/{series}/Group", { series }, { topLevel: true });
      const gid = group?.IDs?.ID;
      if (!Number.isInteger(gid)) throw new ShokoError(`series ${series} has no top-level group`);
      const members = await get("/api/v3/Group/{group}/Series", { group: gid },
        { recursive: true, includeDataFrom: "AniDB", includeMissing: false, randomImages: false });
      const list = Array.isArray(members) ? members : members?.List || [];
      return {
        franchise: { shoko_group_id: gid, name: group.Name, sort_name: group.SortName ?? null, main_series_id: group.IDs?.MainSeries ?? null },
        seasons: list.map((s) => ({
          shoko_series_id: s.IDs?.ID,
          name: s.Name,
          anidb_type: s.AniDB?.Type ?? null,
          air_date: s.AniDB?.AirDate ?? null,
          end_date: s.AniDB?.EndDate ?? null,
          local_episodes: s.Sizes?.Local?.Episodes ?? null,
        })),
      };
    },
  };
}
