-- Label accuracy (#17): which signal proposed each facet, the owner's
-- "labels checked" snapshot, and what made a work's labels.
ALTER TABLE "stills" ADD COLUMN "facet_sources" jsonb;--> statement-breakpoint
ALTER TABLE "stills" ADD COLUMN "label_check" jsonb;--> statement-breakpoint
ALTER TABLE "works" ADD COLUMN "labels_run" jsonb;