CREATE EXTENSION IF NOT EXISTS vector;
--> statement-breakpoint
ALTER TABLE "stills" ADD COLUMN "embedding" vector(768);--> statement-breakpoint
ALTER TABLE "stills" ADD COLUMN "embedding_model" text;