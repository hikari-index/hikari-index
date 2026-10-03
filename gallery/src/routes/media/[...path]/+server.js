// Streams the web images (AVIF) from HIKARI_IMAGES. Resolves and confines the
// path so traversal cannot escape the images root.
//
// Who may fetch what: an image belongs to a still, and a visitor gets only
// the images of stills the pages would show them (picked, not culled, not
// hidden). The pages already hide those stills, but an address saved
// earlier, or guessed, went on answering. A signed-in admin gets any
// still's images (the workbench shows culled and hidden ones). The answer
// is remembered for half a minute per still: a page of thumbnails is one
// lookup each, not one per size.
//
// Caching: an address with ?v=<sha256 prefix> (mediaUrl) names one exact
// file, so the browser may keep it for a year without asking again; a new
// file gets a new address. An address without a version gets an hour and a
// Last-Modified/ETag pair, so a revisit costs a 304, not the whole file.
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { Readable } from "node:stream";
import { error } from "@sveltejs/kit";
import { env } from "$env/dynamic/private";
import { stillIsVisible } from "$lib/server/catalog.js";

const REMEMBER_MS = 30_000;
const seen = new Map(); // "work/candidate" -> { ok, until }

async function visible(work, candidate) {
  const key = `${work}/${candidate}`;
  const hit = seen.get(key);
  if (hit && hit.until > Date.now()) return hit.ok;
  const ok = await stillIsVisible(work, candidate);
  if (seen.size > 20_000) seen.clear();
  seen.set(key, { ok, until: Date.now() + REMEMBER_MS });
  return ok;
}

export async function GET({ params, url, request, locals }) {
  const root = resolve(env.HIKARI_IMAGES ?? "");
  if (!root) error(500, "HIKARI_IMAGES is not set");
  const target = resolve(root, params.path);
  if (!target.startsWith(root + sep) || !target.endsWith(".avif")) {
    error(404);
  }
  // <work>/<candidate>/<size>.avif, and nothing else, is an image address
  // (a control character would also be refused by the database, as a 500).
  const parts = params.path.split("/");
  if (parts.length !== 3 || /[\u0000-\u001f\u007f]/.test(params.path)) error(404);
  const open = await visible(parts[0], parts[1]);
  if (!open && !locals.admin) error(404);
  let info;
  try {
    info = await stat(target);
  } catch {
    error(404);
  }
  const etag = `"${info.size.toString(16)}-${Math.floor(info.mtimeMs).toString(16)}"`;
  const headers = {
    "content-type": "image/avif",
    // An image only an admin may see must not be kept by anything on the
    // way (a shared cache would hand it to the next visitor).
    "cache-control": !open ? "private, no-store" : url.searchParams.has("v") ? "public, max-age=31536000, immutable" : "public, max-age=3600",
    "last-modified": info.mtime.toUTCString(),
    etag,
  };
  const since = request.headers.get("if-modified-since");
  if (request.headers.get("if-none-match") === etag || (!request.headers.has("if-none-match") && since && Date.parse(since) >= Math.floor(info.mtimeMs / 1000) * 1000)) {
    return new Response(null, { status: 304, headers });
  }
  return new Response(Readable.toWeb(createReadStream(target)), {
    headers: { ...headers, "content-length": String(info.size) },
  });
}
