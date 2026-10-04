// The gallery's own worker: the import stage (capability "gallery"), run
// inside the gallery process because it only reads run records and writes
// the database. Off unless HIKARI_RUNS is set (the runs folder), so a
// gallery without the run records never takes an import it cannot do.
// With no import waiting, a pass keeps the repeat marks current (works
// whose series changed since they were last matched; repeats.js).
import { env } from "$env/dynamic/private";
import { ensureMigrated } from "./db/index.js";
import { checkIn, commitJob, failJob, leaseFor, releaseOwn } from "./jobs.js";
import { refreshRepeats } from "./repeats.js";
import { importWork } from "./runs.js";

const ID = "gallery";
const EVERY_MS = 30_000;

async function tick() {
  // The worker starts with the server, before any request has brought the
  // schema up to date; it must not read tables a migration is about to change.
  await ensureMigrated();
  await checkIn(ID, ["gallery"], env.HIKARI_GALLERY_VERSION || null);
  const job = await leaseFor(ID, ["gallery"]);
  if (!job) {
    await refreshRepeats();
    return;
  }
  console.log(`[gallery-worker] import ${job.work_id} (attempt ${job.attempt}/${job.max_attempts})`);
  try {
    const result = await importWork(job.work_id);
    const state = await commitJob(job.id, ID, result);
    console.log(`[gallery-worker] import ${job.work_id}: ${result.stills} stills -> ${state}`);
  } catch (error) {
    const state = await failJob(job, ID, error.errorClass || "transient", error.message);
    console.error(`[gallery-worker] import ${job.work_id}: ${error.message} -> ${state}`);
  }
}

export function startGalleryWorker() {
  if (!env.HIKARI_RUNS || globalThis.__hikariGalleryWorker) return;
  globalThis.__hikariGalleryWorker = true;
  let busy = false;
  const run = async () => {
    if (busy) return;
    busy = true;
    try {
      await tick();
    } catch (error) {
      console.error(`[gallery-worker] ${error.message}`); // database down: try again next tick
    } finally {
      busy = false;
    }
  };
  releaseOwn(ID)
    .catch(() => 0)
    .finally(() => {
      run();
      setInterval(run, EVERY_MS).unref();
    });
}
