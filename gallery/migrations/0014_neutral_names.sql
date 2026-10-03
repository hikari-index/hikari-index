-- Neutral names (2026-10-03). The worker that mounts the video was called
-- after the machine it ran on; now it is the "source" worker. The job
-- params' identity keys take the bundle format 2.4 names.
UPDATE "jobs" SET "capability" = 'source' WHERE "capability" = 'unraid';--> statement-breakpoint
UPDATE "jobs" SET "owner" = 'source' WHERE "owner" = 'unraid' AND "state" IN ('leased', 'running', 'cancel_requested');--> statement-breakpoint
UPDATE "workers" SET "capabilities" = (SELECT COALESCE(jsonb_agg(CASE WHEN x = 'unraid' THEN 'source' ELSE x END), '[]'::jsonb) FROM jsonb_array_elements_text("capabilities") AS x) WHERE "capabilities" ? 'unraid';--> statement-breakpoint
UPDATE "workers" SET "id" = 'source' WHERE "id" = 'unraid' AND NOT EXISTS (SELECT 1 FROM "workers" WHERE "id" = 'source');--> statement-breakpoint
UPDATE "jobs" SET "params" = jsonb_set("params", '{identity}',
  (("params" -> 'identity') - 'shoko_series_id' - 'shoko_episode_ids' - 'shoko_file_id' - 'fixture_id')
  || jsonb_build_object('series_id', "params" -> 'identity' -> 'shoko_series_id', 'episode_ids', "params" -> 'identity' -> 'shoko_episode_ids',
                        'file_id', "params" -> 'identity' -> 'shoko_file_id', 'work_id', "params" -> 'identity' -> 'fixture_id'))
WHERE "params" -> 'identity' ? 'shoko_series_id';
