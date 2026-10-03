CREATE TABLE "franchises" (
	"id" integer PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"sort_name" text,
	"main_series_id" integer,
	"synced_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "seasons" (
	"id" integer PRIMARY KEY NOT NULL,
	"franchise_id" integer NOT NULL,
	"name" text NOT NULL,
	"anidb_type" text,
	"air_date" text,
	"end_date" text,
	"local_episodes" integer
);
--> statement-breakpoint
ALTER TABLE "seasons" ADD CONSTRAINT "seasons_franchise_id_franchises_id_fk" FOREIGN KEY ("franchise_id") REFERENCES "public"."franchises"("id") ON DELETE cascade ON UPDATE no action;