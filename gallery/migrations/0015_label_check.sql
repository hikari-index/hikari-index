-- Label accuracy (#17): which signal proposed each facet and what made
-- the labels, the owner's "labels checked" snapshot.
ALTER TABLE "stills" ADD COLUMN "facet_sources" jsonb;--> statement-breakpoint
ALTER TABLE "stills" ADD COLUMN "labels_run" jsonb;--> statement-breakpoint
ALTER TABLE "stills" ADD COLUMN "label_check" jsonb;--> statement-breakpoint
ALTER TABLE "works" ADD COLUMN "labels_run" jsonb;