CREATE TABLE "text_vectors" (
	"key" text PRIMARY KEY NOT NULL,
	"model_id" text NOT NULL,
	"preprocessing" text NOT NULL,
	"weight_revision" text,
	"embedding" vector(768) NOT NULL,
	"hits" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"last_used_at" timestamp with time zone DEFAULT now() NOT NULL
);
