import { error, json } from "@sveltejs/kit";
import { failJob, jobById } from "$lib/server/jobs.js";
import { UUID, workerFrom } from "$lib/server/worker-api.js";

const CLASSES = new Set(["transient", "input", "source", "cancelled"]);

// POST {error_class, message, retry_now}: end an attempt that did not commit.
export async function POST({ request, params }) {
  const worker = workerFrom(request);
  if (!UUID.test(params.id)) error(400, "bad job id");
  const body = await request.json().catch(() => ({}));
  const job = await jobById(params.id);
  if (!job || job.owner !== worker) error(409, "this stage is not held by this worker");
  const cls = CLASSES.has(body.error_class) ? body.error_class : "transient";
  return json({ state: await failJob(job, worker, cls, String(body.message ?? ""), { retryNow: Boolean(body.retry_now) }) });
}
