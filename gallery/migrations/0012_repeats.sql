ALTER TABLE "stills" ADD COLUMN "repeat_in" jsonb DEFAULT '[]'::jsonb NOT NULL;--> statement-breakpoint
ALTER TABLE "works" ADD COLUMN "repeats_key" text;