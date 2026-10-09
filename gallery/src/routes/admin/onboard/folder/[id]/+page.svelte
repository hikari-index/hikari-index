<script>
  import { invalidateAll } from "$app/navigation";
  let { data, form } = $props();
  const WORK_ID_PATTERN = "[a-z0-9][a-z0-9\\-]{1,71}";
  const l = $derived(data.listing);
  const where = $derived(`${l.root}${l.path ? `/${l.path}` : ""}`);
  const bad = $derived(form?.problems ?? { rows: {} });
  const sent = () => form?.values ?? {};

  // Every field is its own state, started from what the server sent back
  // (the add-a-file page's rule: a one-way value= is a reset waiting for a
  // re-render). Rows are keyed by their place in the listing. An unticked
  // row's fields stay enabled (dimmed), so what was typed in them comes
  // back with a refused submit; the box alone decides what is queued.
  let title = $state(sent().title ?? "");
  let group = $state(sent().group ?? "");
  let budget = $state(sent().budget ?? "balanced");
  const build = (from) =>
    from.map((r) => {
      const v = sent().rows?.[r.i];
      return {
        ...r,
        pick: v ? v.pick : !r.existing && !r.extra,
        episode: v ? v.episode : r.guess == null ? "" : String(r.guess),
        episodeTitle: v ? v.episode_title : "",
        workId: v ? v.work_id : "",
      };
    });
  let rows = $state(build(data.rows));
  // The page refreshes itself while the worker has not answered; when the
  // answer arrives (or another listing is shown) the rows are built again
  // from it. While the same answer stays, edits are kept.
  let builtFor = $state(`${data.listing.id}:${data.listing.state}`);
  $effect(() => {
    const key = `${data.listing.id}:${data.listing.state}`;
    if (key !== builtFor) {
      builtFor = key;
      rows = build(data.rows);
    }
  });
  const picked = $derived(rows.filter((r) => r.pick && !r.existing));
  const joins = $derived(data.catalogue.series.find((s) => s.name.toLowerCase() === title.trim().replace(/\s+/g, " ").toLowerCase()) ?? null);
  const gib = (b) => (b >= 2 ** 30 ? `${(b / 2 ** 30).toFixed(1)} GiB` : `${Math.round(b / 2 ** 20)} MiB`);
  const totalBytes = $derived(picked.reduce((t, r) => t + (r.size || 0), 0));
  const since = (t) => {
    const m = Math.max(0, Math.round((Date.now() - new Date(t).getTime()) / 60000));
    return m < 1 ? "under a minute" : m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  };
  const needsUpdate = $derived(/no stage named/.test(l.errorMessage ?? ""));
  function setAll(on) {
    for (const r of rows) if (!r.existing) r.pick = on;
  }

  // While the worker has not answered, look again every few seconds.
  $effect(() => {
    if (!data.open) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") invalidateAll();
    }, 4000);
    return () => clearInterval(t);
  });
</script>

<svelte:head>
  <title>Add a folder — {where} — Hikari Index</title>
</svelte:head>

<nav class="crumbs"><a href="/admin/onboard/folder">← Add a folder</a> <a href="/admin/onboard">Onboard</a></nav>

<h1>Add a folder <span class="mono where">{where}</span></h1>

{#if form?.message}<p class="note" role="alert">{form.message}</p>{/if}

{#if data.open}
  <p class="note" role="status">Asking the source worker for the folder's files… {#if data.worker?.live}it looks for listings every few seconds, so this is usually quick.{:else if data.worker}the worker has not checked in for {since(data.worker.last_seen_at)}; the listing waits until it is back.{:else}no worker that reads the library has ever checked in.{/if} This page refreshes itself.</p>
  <form method="POST" action="?/cancel"><button type="submit" class="quiet">Never mind</button></form>
{:else if l.state !== "committed"}
  <p class="note" role="alert">
    {#if needsUpdate}The source worker that answered is from before folder listing existed; it needs updating to a build that has it (the add-a-file page still works with it).
    {:else if l.state === "cancelled"}This listing was cancelled{l.errorMessage ? `: ${l.errorMessage.replace(/\.$/, "")}` : ""}.
    {:else}The folder could not be listed{l.errorMessage ? `: ${l.errorMessage.replace(/\.$/, "")}` : ""}.{/if}
  </p>
  <form method="POST" action="?/again" class="inline"><button type="submit">Ask again</button></form>
{:else}
  {@const res = l.result}
  <div class="meta lead-narrow">
    {res.videos} video file{res.videos === 1 ? "" : "s"} in this folder{#if res.folders}, {res.folders} subfolder{res.folders === 1 ? "" : "s"} not entered{/if}{#if res.others}, {res.others} other file{res.others === 1 ? "" : "s"} not listed{/if}.
    {#if res.truncated}Only the first {res.files.length} are shown; this folder holds more than one page can take.{/if}
    Listed {since(l.finishedAt)} ago; if the folder changed since, <form method="POST" action="?/again" class="inline"><button type="submit" class="quiet">ask again</button></form>.
  </div>

  {#if !rows.length}
    <p class="meta" role="status">Nothing here looks like a video file.</p>
  {:else}
    <form method="POST" action="?/queue" class="confirm">
      <fieldset>
        <legend>What they are</legend>
        <label for="title">Series title</label>
        <input id="title" name="title" bind:value={title} required maxlength="200" autocomplete="off" list="local-series" class="wide" aria-describedby="title-help" />
        <datalist id="local-series">{#each data.catalogue.series as s (s.name)}<option value={s.name}></option>{/each}</datalist>
        <span class="why" id="title-help">
          {#if joins}Joins “{joins.name}”, which is already here{joins.title !== joins.name ? `, under ${joins.title}` : ""}.
          {:else}Every ticked file becomes an episode of this one series. A season is its own series; give each its own title. Films go in one at a time, from the add-a-file page.{/if}
        </span>
        {#if bad.title}<span class="why bad" role="alert">{bad.title}</span>{/if}

        <label for="group">File it under <span class="muted">(optional)</span></label>
        <input id="group" name="group" bind:value={group} maxlength="200" autocomplete="off" list="local-titles" class="wide" aria-describedby="group-help" />
        <datalist id="local-titles">{#each data.catalogue.titles as t (t)}<option value={t}></option>{/each}</datalist>
        <span class="why" id="group-help">A wider title to gather several series under, the way a show's seasons sit under the show. Left empty, a new series is its own title.</span>
        {#if bad.group}<span class="why bad" role="alert">{bad.group}</span>{/if}
      </fieldset>

      <div class="tools">
        <span class="meta">{picked.length} of {rows.length} ticked{#if picked.length} · reads {gib(totalBytes)}{/if}</span>
        <button type="button" class="quiet" onclick={() => setAll(true)}>Tick all</button>
        <button type="button" class="quiet" onclick={() => setAll(false)}>Untick all</button>
      </div>
      {#if bad.rows?.[-1]}<p class="why bad" role="alert">{bad.rows[-1]}</p>{/if}

      <table class="data files">
        <thead>
          <tr>
            <th scope="col"><span class="sr">Add</span></th>
            <th scope="col" class="text">File</th>
            <th scope="col" class="text">Episode</th>
            <th scope="col" class="text">Episode title <span class="muted">(optional)</span></th>
            <th scope="col" class="text">Work id <span class="muted">(optional)</span></th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r (r.i)}
            <tr class:off={!r.pick || r.existing}>
              <td>
                {#if r.existing}
                  <span class="sr">already here</span>
                {:else}
                  <input type="checkbox" name={`pick-${r.i}`} bind:checked={r.pick} aria-label={`Add ${r.name}`} />
                {/if}
              </td>
              <th scope="row" class="text">
                <span class="mono wrap">{r.name}</span>
                <span class="why">{gib(r.size)}{#if r.existing} · already queued or in the gallery as <a href={`/admin/jobs#${r.existing.chain_id}`}>{r.existing.work_id}</a>{:else if r.extra} · looks like an opening, ending or extra, so it starts unticked{:else if r.guess == null} · no episode number found in the name; type one{/if}</span>
                {#if bad.rows?.[r.i]}<span class="why bad" role="alert">{bad.rows[r.i]}</span>{/if}
              </th>
              <td class="text">
                {#if !r.existing}<input name={`episode-${r.i}`} bind:value={r.episode} inputmode="numeric" pattern={"\\d{1,4}"} autocomplete="off" class="num" aria-label={`Episode number for ${r.name}`} />{/if}
              </td>
              <td class="text">
                {#if !r.existing}<input name={`episode_title-${r.i}`} bind:value={r.episodeTitle} maxlength="200" autocomplete="off" class="wide" aria-label={`Episode title for ${r.name}`} />{/if}
              </td>
              <td class="text">
                {#if !r.existing}<input name={`work_id-${r.i}`} bind:value={r.workId} pattern={WORK_ID_PATTERN} autocomplete="off" spellcheck="false" class="mono wid" placeholder="from the title and number" aria-label={`Work id for ${r.name}`} />{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>

      <fieldset class="budget">
        <legend>How many stills</legend>
        {#each Object.entries(data.budgets) as [key, b] (key)}
          <label class="choice"><input type="radio" name="budget" value={key} bind:group={budget} /><span>{b.label} <span class="muted">· {b.note}</span></span></label>
        {/each}
        {#if bad.budget}<span class="why bad" role="alert">{bad.budget}</span>{/if}
        <label class="hold"><input type="checkbox" name="hold_chapters" checked={sent().hold_chapters === "on" || sent().hold_chapters === true} /> <span>Hold the opening and ending, if the file has chapters for them <span class="muted">· the frames are kept in the pool, not picked; a chapter named OP or ED, or 85 to 95 seconds long near either end</span></span></label>
      </fieldset>

      <h2>What will run, for each</h2>
      <ol class="list">
        {#each data.stages as s (s.label)}<li>{s.label} <span class="muted">· {s.machine}</span></li>{/each}
      </ol>
      <p class="meta lead-narrow">The works run one after another. The first step checks each file before reading it through: a file with no video, or with more than one video stream, stops there and its row in Jobs says why. Each file gets its fingerprint then. Color is taken from the video's own color tags; the labels come from models trained on anime and have not been measured on anything else.</p>

      <button type="submit" class="go" disabled={!picked.length}>Add {picked.length === 1 ? "this episode" : `${picked.length} episodes`}</button>
    </form>
  {/if}
{/if}

<style>
  .where {
    font-size: var(--t-ui);
    font-weight: 400;
    color: var(--text-2);
    margin-left: var(--s-2);
    overflow-wrap: anywhere;
  }
  .lead-narrow {
    max-width: 720px;
  }
  .inline {
    display: inline;
  }
  .inline button.quiet {
    height: auto;
    padding: 0;
    border: 0;
    background: none;
    font: inherit;
    color: var(--text-2);
    text-decoration: underline;
    text-decoration-color: var(--link-rule);
    cursor: pointer;
  }
  .confirm {
    margin-top: var(--s-4);
  }
  fieldset {
    border: 1px solid var(--line-1);
    padding: var(--s-3) var(--s-4) var(--s-4);
    margin: 0 0 var(--s-4);
    max-width: 720px;
  }
  fieldset > label:not(.choice) {
    display: block;
    margin: var(--s-3) 0 var(--s-1);
  }
  .choice {
    display: flex;
    align-items: center;
    gap: var(--s-2);
  }
  .tools {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--s-3);
    margin: var(--s-3) 0 var(--s-2);
  }
  .files td,
  .files th {
    vertical-align: top;
  }
  .files tr.off th,
  .files tr.off td {
    color: var(--text-3);
  }
  .wrap {
    overflow-wrap: anywhere;
  }
  .wide {
    width: 100%;
  }
  .num {
    width: 5rem;
  }
  .wid {
    width: 100%;
    min-width: 12rem;
  }
  .why {
    display: block;
    color: var(--text-3);
    font-size: var(--t-meta);
    margin-top: var(--s-1);
    font-weight: 400;
  }
  .bad {
    color: var(--accent);
  }
  .muted {
    color: var(--text-3);
    font-weight: 400;
  }
  .quiet {
    color: var(--text-3);
  }
  .list {
    margin: 0 0 var(--s-3);
    padding-left: 1.4em;
  }
  .budget {
    margin-top: var(--s-4);
  }
  .go {
    border-color: var(--accent);
    color: var(--text-1);
    margin-top: var(--s-3);
  }
</style>
