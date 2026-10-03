ALTER TABLE "workers" ADD COLUMN "standby" boolean DEFAULT false NOT NULL;--> statement-breakpoint
UPDATE "jobs" SET "capability" = 'analyze' WHERE "capability" = 'rtx';--> statement-breakpoint
UPDATE "workers" SET "capabilities" = '["analyze"]'::jsonb WHERE "capabilities" = '["rtx"]'::jsonb;
