CREATE TABLE "stills" (
	"id" text PRIMARY KEY NOT NULL,
	"work_id" text NOT NULL,
	"candidate_id" text NOT NULL,
	"shot_id" text,
	"ts_seconds" double precision,
	"source" text DEFAULT 'published' NOT NULL,
	"selected" boolean DEFAULT true NOT NULL,
	"facets" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"tags" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"tiers" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"review_state" text DEFAULT 'unreviewed' NOT NULL,
	"locked" boolean DEFAULT false NOT NULL,
	"excluded" boolean DEFAULT false NOT NULL,
	"review_note" text,
	"reviewed_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "works" (
	"id" text PRIMARY KEY NOT NULL,
	"title" text NOT NULL,
	"entry_type" text NOT NULL,
	"episode" integer,
	"episode_title" text,
	"shoko_series_id" integer,
	"shoko_episode_ids" jsonb,
	"shoko_file_id" integer,
	"bundle_id" text,
	"selection" jsonb,
	"imported_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
ALTER TABLE "stills" ADD CONSTRAINT "stills_work_id_works_id_fk" FOREIGN KEY ("work_id") REFERENCES "public"."works"("id") ON DELETE cascade ON UPDATE no action;