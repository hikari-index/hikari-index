CREATE TABLE "removals" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"scope" text NOT NULL,
	"target" text NOT NULL,
	"label" text NOT NULL,
	"reason" text,
	"works" jsonb NOT NULL,
	"requested_by" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
