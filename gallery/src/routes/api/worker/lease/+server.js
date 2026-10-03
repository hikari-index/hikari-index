import { json } from "@sveltejs/kit";
import { checkIn, leaseFor, parentChain, regularWorkerOn, releaseOwn } from "$lib/server/jobs.js";
import { API_CAPABILITIES, workerFrom } from "$lib/server/worker-api.js";

// POST {version, starting, standby}: check in, and take the oldest runnable
// stage this worker can run, with the stages before it (their results say
// where the inputs are). `starting: true` first returns anything this
// worker held when it last stopped. `standby: true` takes a stage only
// while no regular analyze worker is on; the reply says who it waits for.
export async function POST({ request }) {
  const worker = workerFrom(request);
  const body = await request.json().catch(() => ({}));
  const standby = body.standby === true;
  await checkIn(worker, API_CAPABILITIES, typeof body.version === "string" ? body.version.slice(0, 80) : null, standby);
  const released = body.starting ? await releaseOwn(worker) : 0;
  if (standby) {
    const regular = await regularWorkerOn(worker, API_CAPABILITIES[0]);
    if (regular) return json({ job: null, released, standing_by_for: regular });
  }
  const job = await leaseFor(worker, API_CAPABILITIES);
  if (!job) return json({ job: null, released });
  const chain = (await parentChain(job)).map((p) => ({ id: p.id, stage: p.stage, state: p.state, result: p.result }));
  return json({
    released,
    job: { id: job.id, stage: job.stage, work_id: job.work_id, attempt: job.attempt, max_attempts: job.max_attempts, params: job.params, chain },
  });
}
