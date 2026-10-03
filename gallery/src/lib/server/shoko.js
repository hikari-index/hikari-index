// The app's Shoko connection: ./shoko-client.js with its settings from the
// environment. HIKARI_SHOKO_BASE_URL is the server; the key comes from the
// file HIKARI_SHOKO_API_KEY_FILE names (mounted read-only, e.g. at
// /run/secrets/shoko-api-key). Shoko is read from the Linux control plane
// only (the gallery container), never from the Windows dev server by
// convenience; without these settings the Onboard page says so.
import { env } from "$env/dynamic/private";
import { client, readKey, ShokoError } from "./shoko-client.js";

export { ShokoError };

export function shokoConfigured() {
  return Boolean(env.HIKARI_SHOKO_BASE_URL && env.HIKARI_SHOKO_API_KEY_FILE);
}

let cached;
let checkedAt = 0;

// The client, after confirming the key is a non-admin user (re-checked every
// ten minutes so a rotated key is noticed).
export async function shoko() {
  if (!shokoConfigured()) throw new ShokoError("Shoko is not configured for this gallery");
  if (!cached || Date.now() - checkedAt > 10 * 60 * 1000) {
    const c = client({ baseUrl: env.HIKARI_SHOKO_BASE_URL, key: readKey({ keyFile: env.HIKARI_SHOKO_API_KEY_FILE }) });
    await c.whoami();
    cached = c;
    checkedAt = Date.now();
  }
  return cached;
}
