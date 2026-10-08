// Schema v1: what the contact sheet and the workbench's first actions need
// and nothing more. Works and stills mirror the pipeline's outputs; the
// review columns are the only state that originates here.
//
// Migrations are generated from this file by drizzle-kit into ./migrations
// and applied by the app at startup (src/lib/server/db/index.js). Never
// hand-edit the database.
import {
  boolean,
  doublePrecision,
  integer,
  jsonb,
  pgSequence,
  pgTable,
  text,
  timestamp,
  uuid,
  vector,
} from "drizzle-orm/pg-core";

// Shoko's hierarchy, snapshotted at sync time: a top-level Group is the
// franchise, each Series in it is a season, film or special. Seasons are
// listed whether or not anything has been extracted from them, so the
// library reads the way Shoko's does. The gallery never calls Shoko while
// someone browses.
//
// A work added from a local file, without Shoko (ADR-0014), sits in the
// same two tables: its title and series get ids below zero from
// local_id_seq, typed by the operator at onboarding. Shoko's ids are
// positive, so the two never meet, and everything keyed on a series
// (Library grouping, similarity scopes, repeats, removal) treats both alike.
export const localIds = pgSequence("local_id_seq");

export const franchises = pgTable("franchises", {
  id: integer("id").primaryKey(), // Shoko group id; below zero: a local title
  name: text("name").notNull(),
  sortName: text("sort_name"),
  mainSeriesId: integer("main_series_id"),
  syncedAt: timestamp("synced_at", { withTimezone: true }).defaultNow().notNull(),
});

export const seasons = pgTable("seasons", {
  id: integer("id").primaryKey(), // Shoko series id; below zero: a local series
  franchiseId: integer("franchise_id").notNull().references(() => franchises.id, { onDelete: "cascade" }),
  name: text("name").notNull(),
  anidbType: text("anidb_type"), // TV Series, Movie, OVA, ...
  airDate: text("air_date"), // ISO date as Shoko reports it; ordering only
  endDate: text("end_date"),
  localEpisodes: integer("local_episodes"),
});

export const works = pgTable("works", {
  // The work slug the pipeline run was named by (also the URL segment).
  id: text("id").primaryKey(),
  title: text("title").notNull(),
  entryType: text("entry_type").notNull(), // "episode" | "movie"
  episode: integer("episode"),
  episodeTitle: text("episode_title"),
  // The owner's episode title, beside Shoko's and never over it: import and
  // a re-read from Shoko refresh episode_title and leave this alone. Pages
  // show this one when it is set.
  episodeTitleHuman: text("episode_title_human"),
  shokoSeriesId: integer("shoko_series_id"),
  shokoEpisodeIds: jsonb("shoko_episode_ids").$type(),
  shokoFileId: integer("shoko_file_id"),
  bundleId: text("bundle_id"),
  // The picker's record for this work (selector, budget, weights, picks),
  // stored whole so a re-run is comparable with the last one.
  selection: jsonb("selection").$type(),
  // Which set of sibling works (same Shoko series) this work's stills were
  // last matched against for repeats (stills.repeat_in): a hash of every
  // sibling's id and bundle, itself included. Null after an import; when it
  // differs from the current set, the gallery matches the work again
  // (lib/server/repeats.js, ADR-0012).
  repeatsKey: text("repeats_key"),
  // What made this work's labels, from the analyze record's proposals
  // (taxonomy, tagger allowlist, fusion version and cut-offs). Copied into
  // a still's label check so the accuracy report can tell runs apart.
  labelsRun: jsonb("labels_run").$type(),
  importedAt: timestamp("imported_at", { withTimezone: true }).defaultNow().notNull(),
});

export const stills = pgTable("stills", {
  // "<work>/<candidate>".
  id: text("id").primaryKey(),
  workId: text("work_id").notNull().references(() => works.id, { onDelete: "cascade" }),
  candidateId: text("candidate_id").notNull(),
  shotId: text("shot_id"),
  tsSeconds: doublePrecision("ts_seconds"),
  // "published" (in the bundle) or "surplus" (kept beside it).
  source: text("source").notNull().default("published"),
  // Whether the production selector picked it. Unselected pool frames may
  // be imported later for the workbench; the sheet shows selected only.
  selected: boolean("selected").notNull().default(true),
  facets: jsonb("facets").$type().notNull().default({}),
  tags: jsonb("tags").$type().notNull().default([]),
  tiers: jsonb("tiers").$type().notNull().default([]),
  // Compact palette from the color descriptor: representative swatches,
  // shadow/midtone/highlight swatches, hue-band masses, luma percentiles,
  // weighted saturation, Cb/Cr temperature. Null when the descriptor
  // abstained or the frame was never measured (surplus picks).
  palette: jsonb("palette").$type(),
  // SigLIP 2 image embedding (768-d, unit-normalised) for "find similar"
  // (research/02: one vector, SigLIP, exact cosine search first; an
  // approximate index only when latency or load justifies it). The
  // extension is created by migration 0002 alongside this column.
  embedding: vector("embedding", { dimensions: 768 }),
  embeddingModel: text("embedding_model"),
  // The owner's corrections, stored beside the machine's values, never over
  // them: a re-import refreshes facets/tags and leaves these alone, and a
  // page can always say which is which. facetsHuman maps family -> value
  // (or null to clear a machine value); tagsHuman is {add: [], remove: []}.
  facetsHuman: jsonb("facets_human").$type(),
  tagsHuman: jsonb("tags_human").$type(),
  // Which signal proposed each machine facet and its score, from the run:
  // {family: {s: "face-occupancy", p: 0.42}}. Null for runs made before
  // fusion recorded it.
  facetSources: jsonb("facet_sources").$type(),
  // The work's labels_run as of the import that wrote these facets, kept
  // on the still so a label check always pairs labels with what made them.
  labelsRun: jsonb("labels_run").$type(),
  // The owner's "labels checked" mark: every label of the still was looked
  // at, so what was left as proposed counts as agreement. A snapshot taken
  // at that save, so a later re-import or edit does not change what was
  // judged: {at, run, machine: {family: value}, sources, human: {family:
  // value|null}}. Null when unchecked. The accuracy report reads these.
  labelCheck: jsonb("label_check").$type(),
  // Review state, the reason this table exists:
  //   unreviewed | kept | culled   (culled = credits, cards, junk the human removed)
  // Reasons to look at this still before the rest (research/03 "review
  // only threshold/disagreement/sensitive exceptions"), set by import from
  // the run records: [{k: "text", tags}, {k: "rating", score}, {k: "unsure",
  // label, score}, {k: "head" | "scale", label, score, source}]. Empty for most
  // stills; the Worth-a-look page lists the rest.
  reviewReasons: jsonb("review_reasons").$type().notNull().default([]),
  // The other works of the same Shoko series this frame repeats in (an
  // opening, an ending, a logo, a reused cut), as work ids; found by
  // matching the works' pools against each other (ADR-0012). A mark for
  // the reviewer: nothing is hidden or culled because of it.
  repeatIn: jsonb("repeat_in").$type().notNull().default([]),
  reviewState: text("review_state").notNull().default("unreviewed"),
  // Operator pin (research/02): survive re-selection / never be chosen.
  locked: boolean("locked").notNull().default(false),
  excluded: boolean("excluded").notNull().default(false),
  reviewNote: text("review_note"),
  reviewedAt: timestamp("reviewed_at", { withTimezone: true }),
});

// Mood-search phrase vectors, so a phrase typed once never needs the text
// encoder again (the encoder host may be off). Keyed by a hash of the exact
// phrase with the model and tokenizer setup that encoded it, never by the
// phrase itself: a mood query is the user's business, and the encoder logs
// none either. A different encoder writes different keys, never mixing
// vector spaces.
export const textVectors = pgTable("text_vectors", {
  key: text("key").primaryKey(), // sha256("<model>\n<preprocessing>\n<phrase>")
  modelId: text("model_id").notNull(),
  preprocessing: text("preprocessing").notNull(),
  weightRevision: text("weight_revision"),
  embedding: vector("embedding", { dimensions: 768 }).notNull(),
  hits: integer("hits").notNull().default(0),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  lastUsedAt: timestamp("last_used_at", { withTimezone: true }).defaultNow().notNull(),
});

// Onboarding work (research/01 "Recoverable job protocol"). The Onboard page
// only inserts rows; workers lease the stages they can run and nothing runs
// a media tool inside a web request. One row per stage attempt-series; a
// chain is the stages one onboarding created (chain_id = its first job) and
// a stage becomes runnable when its parent has committed.
//
//   state: queued -> leased -> running -> committed, with retry_wait,
//          blocked (needs the operator), cancel_requested, cancelled,
//          superseded, and terminal dead_letter / ineligible.
//   capability: which worker may run it. "source" = the source worker (the
//          only one that opens raw source; ffmpeg, derivatives); "analyze"
//          = the analyze workers (models, fusion, picker: the Windows RTX
//          box, or a CPU box on standby; "rtx" until migration 0007);
//          "gallery" = the app
//          itself (import). A queued stage with no live worker of that kind
//          is waiting_for_capability (research/04), not an error: the
//          Windows box being off is normal.
//   idempotency_key: stage + source fingerprint + policy version, so a
//          second click (or a duplicate delivery) finds the existing row.
export const jobs = pgTable("jobs", {
  id: uuid("id").primaryKey().defaultRandom(),
  chainId: uuid("chain_id").notNull(),
  parentId: uuid("parent_id"),
  stage: text("stage").notNull(), // extract | analyze | derive | import
  capability: text("capability").notNull(),
  state: text("state").notNull().default("queued"),
  idempotencyKey: text("idempotency_key").notNull().unique(),
  workId: text("work_id").notNull(), // the slug the run, bundle and gallery use
  // Everything the stage needs, snapshotted at onboarding: the Shoko
  // identity, source facts, policy. Private (real ids and paths); the LAN
  // database only.
  params: jsonb("params").$type().notNull(),
  result: jsonb("result").$type(),
  attempt: integer("attempt").notNull().default(0),
  maxAttempts: integer("max_attempts").notNull().default(3),
  owner: text("owner"),
  leaseExpiresAt: timestamp("lease_expires_at", { withTimezone: true }),
  heartbeatAt: timestamp("heartbeat_at", { withTimezone: true }),
  notBefore: timestamp("not_before", { withTimezone: true }),
  errorClass: text("error_class"), // transient | input | operator | ...
  errorMessage: text("error_message"),
  requestedBy: text("requested_by"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  startedAt: timestamp("started_at", { withTimezone: true }),
  finishedAt: timestamp("finished_at", { withTimezone: true }),
});

// One row per removal the owner made (an episode, a season or a whole
// title), kept after everything it names is gone, so "was this ever in
// here, and why not now?" has an answer. It never blocks onboarding the
// same thing again (the LAN gallery; the public build's withdrawals are a
// different, stricter thing, research/07). `works` holds what each removed
// work was: its id, title and episode, its Shoko file and fingerprint, its
// still and review counts, and a summary of the job chains that made it,
// whose rows are deleted with it. The disk half runs as one `scrub` job
// per work (params.removal.id = this id).
export const removals = pgTable("removals", {
  id: uuid("id").primaryKey().defaultRandom(),
  scope: text("scope").notNull(), // work | season | franchise
  target: text("target").notNull(), // the work id, or the Shoko series / group id
  label: text("label").notNull(),
  reason: text("reason"),
  works: jsonb("works").$type().notNull(),
  requestedBy: text("requested_by"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
});

// Workers check in here when they poll, so the jobs page can say which
// capabilities are live ("the Windows box is off" is a normal state).
export const workers = pgTable("workers", {
  id: text("id").primaryKey(), // one row per machine, e.g. "source"
  capabilities: jsonb("capabilities").$type().notNull().default([]),
  version: text("version"),
  // A standby worker takes a stage only while no regular worker of the
  // same kind has checked in recently (an always-on CPU machine standing
  // in for a GPU machine that is off).
  standby: boolean("standby").notNull().default(false),
  // The names of the source folders this worker has mounted (the keys of
  // its HIKARI_SOURCE_ROOTS, never the paths), reported at check-in by a
  // worker that opens raw source. The local-file onboarding page offers
  // them; null from a worker that reports none.
  sourceRoots: jsonb("source_roots").$type(),
  lastSeenAt: timestamp("last_seen_at", { withTimezone: true }).defaultNow().notNull(),
});
