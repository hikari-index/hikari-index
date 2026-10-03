// A preview of a pool frame, made from its master on request: the pool
// has no web images (only picked stills get them), and making a tier for
// every pool frame would be tens of thousands of files for a view opened
// now and then. Admin only (every /admin path needs the session). A small
// in-memory cache keeps the last few hundred previews, keyed by the
// master's path, mtime and size, so a re-extraction's new master gets a
// new one; the browser revalidates every time (no-cache + ETag), which is
// a 304 while the master is the same. A conversion in flight is shared.
import { createHash } from "node:crypto";
import { error } from "@sveltejs/kit";
import sharp from "sharp";
import { poolMaster } from "$lib/server/pool.js";

const WIDTH = 320;
const KEEP = 400; // ~15 KB each
const cache = new Map();
const inFlight = new Map();

export async function GET({ params, request }) {
  const tagOf = (key) => `"${createHash("sha1").update(key).digest("hex").slice(0, 16)}"`;
  const asked = request.headers.get("if-none-match");
  // What is already in hand for this exact master, captured when decided,
  // so an eviction while the handle closes cannot leave nothing.
  let held = null;
  const m = await poolMaster(params.work, params.candidate, (key) => {
    if (asked === tagOf(key)) return false;
    held = cache.get(key) ?? inFlight.get(key) ?? null;
    return !held;
  });
  if (!m) error(404);
  const key = m.key;
  const etag = tagOf(key);
  const headers = { "content-type": "image/jpeg", "cache-control": "private, no-cache", etag };
  if (asked === etag) return new Response(null, { status: 304, headers });
  let body = held instanceof Promise ? null : held;
  if (!body) {
    let pending = held instanceof Promise ? held : inFlight.get(key);
    if (!pending) {
      if (!m.bytes) error(500, "could not read that frame; reload");
      pending = sharp(m.bytes).resize({ width: WIDTH, withoutEnlargement: true }).jpeg({ quality: 72 }).toBuffer().finally(() => inFlight.delete(key));
      inFlight.set(key, pending);
    }
    try {
      body = await pending;
    } catch (e) {
      error(500, `could not read that frame: ${e.message}`);
    }
    cache.set(key, body);
    if (cache.size > KEEP) cache.delete(cache.keys().next().value);
  }
  return new Response(body, { headers: { ...headers, "content-length": String(body.length) } });
}
