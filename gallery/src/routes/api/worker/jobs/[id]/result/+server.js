import { gunzipSync } from "node:zlib";
import { error, json } from "@sveltejs/kit";
import { commitJob, failJob, jobById } from "$lib/server/jobs.js";
import { ImportError, storeAnalyze } from "$lib/server/runs.js";
import { UUID, workerFrom } from "$lib/server/worker-api.js";

const MAX_UNPACKED = 512 * 1024 * 1024;

// POST, body gzip-compressed JSON {files: [{path, sha256, text}], summary}:
// the analyze stage's records. They are checked and stored beside the run
// (runs/<work>/analyze, on the array), then the stage commits with the
// selection's path, which the derive stage reads.
export async function POST({ request, params }) {
  const worker = workerFrom(request);
  if (!UUID.test(params.id)) error(400, "bad job id");
  const job = await jobById(params.id);
  if (!job || job.owner !== worker || !["running", "leased", "cancel_requested"].includes(job.state)) {
    error(409, "this stage is not held by this worker");
  }
  if (job.stage !== "analyze") error(400, `results for stage ${job.stage} do not come through this API`);
  let doc;
  try {
    doc = JSON.parse(gunzipSync(Buffer.from(await request.arrayBuffer()), { maxOutputLength: MAX_UNPACKED }).toString("utf8"));
  } catch {
    error(400, "the body must be gzip-compressed JSON");
  }
  if (!Array.isArray(doc.files) || !doc.files.some((f) => f.path === "selection.json")) error(400, "no selection.json in the result");
  let stored;
  try {
    stored = storeAnalyze(job.work_id, job.id, doc.files);
  } catch (e) {
    if (e instanceof ImportError) {
      const state = await failJob(job, worker, e.errorClass, e.message);
      return json({ state, error: e.message }, { status: 422 });
    }
    throw e;
  }
  const summary = doc.summary && typeof doc.summary === "object" ? doc.summary : {};
  const state = await commitJob(job.id, worker, { ...summary, analyze: stored, selection: `${stored}/selection.json`, files: doc.files.length });
  return json({ state, stored });
}
