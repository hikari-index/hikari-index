import { error, json } from "@sveltejs/kit";
import { beat } from "$lib/server/jobs.js";
import { UUID, workerFrom } from "$lib/server/worker-api.js";

// Keeps the lease; the reply's state tells the worker about a cancel
// (cancel_requested) or that the stage is no longer its own (null).
export async function POST({ request, params }) {
  const worker = workerFrom(request);
  if (!UUID.test(params.id)) error(400, "bad job id");
  return json({ state: await beat(params.id, worker) });
}
