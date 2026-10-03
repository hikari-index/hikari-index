// Authentication and shared rules for the worker API (/api/worker/*), the
// only way the analyze workers reach the job table (ADR-0004: results
// return through the control plane, not by writing the share; maintainer
// decision 2026-09-28: no database credential on a worker machine).
//
// One token per worker machine: HIKARI_WORKER_TOKENS = "name:token,name:token"
// (tokens 32+ characters). A worker sends `Authorization: Bearer <its token>`
// and `X-Hikari-Worker: <its name>`, and a token is good only for its own
// name, so one machine cannot act as another and each can be cut off alone
// (maintainer decision 2026-09-29; it replaced a single shared token).
// API workers can only take "analyze" stages.
import { timingSafeEqual } from "node:crypto";
import { error } from "@sveltejs/kit";
import { env } from "$env/dynamic/private";

export const API_CAPABILITIES = ["analyze"];

const NAME = /^[a-z0-9][a-z0-9-]{1,39}$/;

let parsed = { from: null, table: new Map(), problem: null };

function tokens() {
  const raw = env.HIKARI_WORKER_TOKENS || "";
  if (parsed.from === raw) return parsed;
  const table = new Map();
  let problem = null;
  for (const entry of raw.split(",").map((e) => e.trim()).filter(Boolean)) {
    const at = entry.indexOf(":");
    const name = at > 0 ? entry.slice(0, at) : "";
    const token = at > 0 ? entry.slice(at + 1) : "";
    if (!NAME.test(name)) problem = "an entry in HIKARI_WORKER_TOKENS has no valid worker name before its colon";
    else if (token.length < 32) problem = `the HIKARI_WORKER_TOKENS entry for ${name} is shorter than 32 characters`;
    else if (table.has(name)) problem = `HIKARI_WORKER_TOKENS names ${name} twice`;
    else table.set(name, Buffer.from(token));
  }
  if (!table.size && !problem) problem = "HIKARI_WORKER_TOKENS is not set";
  parsed = { from: raw, table, problem };
  if (problem) console.error(`[worker-api] ${problem}`);
  return parsed;
}

export function workerFrom(request) {
  const { table, problem } = tokens();
  if (problem) error(503, `the worker API is not configured on this gallery: ${problem}`);
  const id = request.headers.get("x-hikari-worker") || "";
  if (!NAME.test(id)) error(400, "bad worker name");
  const got = Buffer.from((request.headers.get("authorization") || "").replace(/^Bearer\s+/i, ""));
  const want = table.get(id);
  // Compared even for an unknown name, so a reply's timing does not say
  // which names exist.
  const against = want ?? Buffer.alloc(got.length || 1);
  const same = got.length === against.length && timingSafeEqual(got, against);
  if (!want || !same) error(401, `bad worker token for ${id}`);
  return id;
}

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
