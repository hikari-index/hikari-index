CREATE TABLE "jobs" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"chain_id" uuid NOT NULL,
	"parent_id" uuid,
	"stage" text NOT NULL,
	"capability" text NOT NULL,
	"state" text DEFAULT 'queued' NOT NULL,
	"idempotency_key" text NOT NULL,
	"work_id" text NOT NULL,
	"params" jsonb NOT NULL,
	"result" jsonb,
	"attempt" integer DEFAULT 0 NOT NULL,
	"max_attempts" integer DEFAULT 3 NOT NULL,
	"owner" text,
	"lease_expires_at" timestamp with time zone,
	"heartbeat_at" timestamp with time zone,
	"not_before" timestamp with time zone,
	"error_class" text,
	"error_message" text,
	"requested_by" text,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	"started_at" timestamp with time zone,
	"finished_at" timestamp with time zone,
	CONSTRAINT "jobs_idempotency_key_unique" UNIQUE("idempotency_key")
);
--> statement-breakpoint
CREATE TABLE "workers" (
	"id" text PRIMARY KEY NOT NULL,
	"capabilities" jsonb DEFAULT '[]'::jsonb NOT NULL,
	"version" text,
	"last_seen_at" timestamp with time zone DEFAULT now() NOT NULL
);
