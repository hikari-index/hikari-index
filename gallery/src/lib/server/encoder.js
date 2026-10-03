// Client for the text encoder service (the text-encoder service in
// compose.yaml): turns a mood query into a SigLIP 2 text
// vector in the same space as the stored image vectors. The gallery never
// runs a model itself (research/02). Every vector is kept in the database
// (text_vectors), so a phrase typed before still works while the encoder is
// down; a new phrase with no encoder makes mood search unavailable and the
// page says so. Tag search keeps working either way.
import { createHash } from "node:crypto";
import { eq, sql } from "drizzle-orm";
import { env } from "$env/dynamic/private";
import { db, schema } from "./db/index.js";
import { readOnly } from "./mode.js";

const TIMEOUT_MS = 8000;
const MAX_TEXT = 512;
// The space the stored image vectors live in. A vector from any other model
// or tokenizer setup is meaningless against them, so it is refused.
const MODEL_ID = "google/siglip2-base-patch16-224";
const PREPROCESSING = "siglip2-tokenizer-pad-max-length-64-v1";

const memo = new Map(); // key -> vector; saves a database round trip on repeats
const MEMO_MAX = 512;

export function encoderUrl() {
  return (env.ENCODER_URL || "").replace(/\/$/, "");
}

export async function encoderHealthy() {
  const base = encoderUrl();
  if (!base) return false;
  try {
    const r = await fetch(`${base}/health`, { signal: AbortSignal.timeout(2000) });
    return r.ok;
  } catch {
    return false;
  }
}

// The exact phrase is the key (the tokenizer is case-sensitive, so "Rain"
// and "rain" are different vectors).
function keyFor(phrase) {
  return createHash("sha256").update(`${MODEL_ID}\n${PREPROCESSING}\n${phrase}`).digest("hex");
}

function remember(key, out) {
  memo.set(key, out);
  if (memo.size > MEMO_MAX) memo.delete(memo.keys().next().value);
  return out;
}

// Returns { embedding, model, cached } or null when the phrase was never
// encoded and the encoder is not reachable.
export async function encodeText(text) {
  const q = String(text || "").trim().slice(0, MAX_TEXT);
  if (!q) return null;
  const key = keyFor(q);
  const t = schema.textVectors;

  const seen = memo.get(key);
  if (seen) {
    touch(key);
    return seen;
  }
  try {
    const [row] = await db().select({ embedding: t.embedding }).from(t).where(eq(t.key, key));
    if (row) {
      touch(key);
      return remember(key, { embedding: row.embedding, model: MODEL_ID, cached: true });
    }
  } catch (error) {
    console.error("[encoder] phrase cache read failed:", error.message);
  }

  const base = encoderUrl();
  if (!base) return null;
  let body;
  try {
    const r = await fetch(`${base}/encode`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text: q }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!r.ok) return null;
    body = await r.json();
  } catch {
    return null;
  }
  if (!Array.isArray(body.embedding) || body.embedding.length !== 768) return null;
  if (body.model_id !== MODEL_ID || body.preprocessing_identity !== PREPROCESSING) {
    console.error(`[encoder] refused a vector from ${body.model_id} / ${body.preprocessing_identity}; the stored images are ${MODEL_ID} / ${PREPROCESSING}`);
    return null;
  }
  // Read-only mode writes nothing: a new phrase is encoded each time it is
  // not in this process's memory.
  if (!readOnly()) {
    try {
      await db()
        .insert(t)
        .values({ key, modelId: MODEL_ID, preprocessing: PREPROCESSING, weightRevision: body.weight_revision ?? null, embedding: body.embedding, hits: 1 })
        .onConflictDoNothing();
    } catch (error) {
      console.error("[encoder] phrase cache write failed:", error.message);
    }
  }
  return remember(key, { embedding: body.embedding, model: MODEL_ID, cached: false });
}

// Use count and last use, for pruning later; never blocks the page.
function touch(key) {
  if (readOnly()) return;
  const t = schema.textVectors;
  db()
    .update(t)
    .set({ hits: sql`${t.hits} + 1`, lastUsedAt: sql`now()` })
    .where(eq(t.key, key))
    .catch(() => {});
}
