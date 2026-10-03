CREATE SEQUENCE "public"."local_id_seq" INCREMENT BY 1 MINVALUE 1 MAXVALUE 9223372036854775807 START WITH 1 CACHE 1;--> statement-breakpoint
ALTER TABLE "workers" ADD COLUMN "source_roots" jsonb;