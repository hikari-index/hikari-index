-- Chains queued before params.purpose existed (2026-09-30) carry only the
-- flag of the button that queued them. Give them the purpose so the Jobs
-- page reads every chain the same way. A palette regeneration's root is its
-- palette stage; a re-read's root is a lone import stage.
UPDATE "jobs" SET "params" = jsonb_set("params", '{purpose}', '"repalette"')
WHERE NOT "params" ? 'purpose'
  AND "chain_id" IN (SELECT "chain_id" FROM "jobs" WHERE "stage" = 'palette' AND "parent_id" IS NULL);
--> statement-breakpoint
UPDATE "jobs" SET "params" = jsonb_set("params", '{purpose}', '"reread"')
WHERE NOT "params" ? 'purpose'
  AND "params" ? 'reread'
  AND "chain_id" IN (SELECT "chain_id" FROM "jobs" j WHERE j."stage" = 'import' AND j."parent_id" IS NULL
    AND NOT EXISTS (SELECT 1 FROM "jobs" o WHERE o."chain_id" = j."chain_id" AND o."id" <> j."id"));
