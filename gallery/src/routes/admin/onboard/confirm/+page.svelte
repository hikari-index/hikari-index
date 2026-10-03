<script>
  import { shownEpisodeTitle } from "$lib/titles.js";
  let { data, form } = $props();
  const gib = (b) => `${(b / 2 ** 30).toFixed(1)} GiB`;
  const WORK_ID_PATTERN = "[a-z0-9][a-z0-9\\-]{1,71}";

  const ready = $derived((data.rows ?? []).filter((r) => !r.blockers.length && !r.existing));
  const left = $derived((data.rows ?? []).filter((r) => r.blockers.length || r.existing));
  let budget = $state("balanced");
  $effect.pre(() => {
    if (form?.budget) budget = form.budget;
  });

  const totals = $derived.by(() => {
    const bytes = ready.reduce((t, r) => t + (r.source.size || 0), 0);
    const minutes = ready.reduce((t, r) => t + (r.source.media.duration_seconds || 0) / 60, 0);
    return { bytes, minutes };
  });
  function hours(seconds) {
    const m = Math.max(1, Math.round(seconds / 60));
    return m < 90 ? `${m} min` : `${(m / 60).toFixed(1)} h`;
  }
  const estimate = $derived.by(() => {
    const e = data.estimate;
    if (!e || !totals.minutes) return null;
    return {
      time: hours(e.secondsPerMinute * totals.minutes),
      stills: Math.round(e.stillsPerMinute * totals.minutes * data.budgets[budget].factor),
      runs: e.runs,
    };
  });
  function label(r) {
    const id = r.identity;
    if (!id) return r.fileName ?? `Shoko file ${r.fileId}`;
    return id.entry_type === "movie" ? id.series_title : `${id.series_title} · episode ${id.episode ?? "?"}`;
  }
  const video = (m) => [m.codec, m.width && `${m.width}×${m.height}`, m.bit_depth && `${m.bit_depth}-bit`].filter(Boolean).join(" · ");
</script>

<svelte:head>
  <title>Confirm onboarding — Hikari Index</title>
</svelte:head>

<nav class="crumbs">
  <a href="/admin/onboard">← Onboard</a>
  {#if data.back}<a href={`/admin/onboard/series/${data.back}`}>back to the series</a>{/if}
</nav>

{#if data.error}
  <p class="note" role="alert">{data.error}</p>
{:else}
  <h1>Confirm {ready.length === 1 ? "one onboarding" : `${ready.length} onboardings`}</h1>

  {#if form?.message}<p class="note" role="alert">{form.message}</p>{/if}

  {#if ready.length}
    <form method="POST" class="confirm">
      <table class="data">
        <thead>
          <tr><th scope="col">Work</th><th scope="col" class="text">Source</th><th scope="col" class="text">Work id <span class="muted">(the name in addresses and run folders)</span></th></tr>
        </thead>
        <tbody>
          {#each ready as r (r.fileId)}
            <tr>
              <th scope="row">
                {label(r)}
                {#if shownEpisodeTitle(r.identity?.entry_type, r.identity?.episode_title)}<span class="why">{shownEpisodeTitle(r.identity?.entry_type, r.identity?.episode_title)}</span>{/if}
              </th>
              <td class="text">
                <span class="mono">{gib(r.source.size)}</span> · {video(r.source.media)} · {Math.round((r.source.media.duration_seconds || 0) / 60)} min
                <details class="more">
                  <summary>file and fingerprint</summary>
                  <span class="mono wrap">{r.identity.source_filename}</span>
                  <span class="mono wrap">{r.source.fingerprint}</span>
                  <span class="mono">series {r.identity.series_id} · episode {r.identity.episode_ids.join(", ")} · file {r.identity.file_id}</span>
                </details>
                {#each r.notes as n (n)}<span class="why">{n}</span>{/each}
              </td>
              <td class="text">
                <input type="hidden" name="file" value={r.fileId} />
                <input
                  name={`work_id-${r.fileId}`}
                  aria-label={`Work id for ${label(r)}`}
                  value={form?.workIds?.[r.fileId] ?? r.workId}
                  required
                  pattern={WORK_ID_PATTERN}
                  autocomplete="off"
                  spellcheck="false"
                  class="wid"
                />
                {#if form?.problems?.[r.fileId]}<span class="why bad" role="alert">{form.problems[r.fileId]}</span>
                {:else if r.workIdRemoving && !form}<span class="why bad">“{r.workId}” is still being removed; onboard it once its old files are deleted (Jobs).</span>
                {:else if r.workIdTaken && !form}<span class="why bad">“{r.workId}” is already used; change it before confirming.</span>{/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>

      <fieldset class="budget">
        <legend>How many stills</legend>
        {#each Object.entries(data.budgets) as [key, b] (key)}
          <label><input type="radio" name="budget" value={key} bind:group={budget} /> {b.label} <span class="muted">· {b.note}</span></label>
        {/each}
        <p class="meta">The pick chooses from every frame the extraction kept, so this costs no extra decoding; each extra still does still get its labels, palette and web images (a few seconds each).</p>
      </fieldset>

      <p class="sum">
        <strong>{ready.length} {ready.length === 1 ? "work" : "works"}</strong>
        · reads {gib(totals.bytes)} of video ({Math.round(totals.minutes)} min)
        {#if estimate}· about {estimate.time} of machine time · about {estimate.stills} stills <span class="muted">(from the last {estimate.runs} onboardings here)</span>{/if}
      </p>

      <h2>What will run, for each</h2>
      <ol class="list">
        {#each data.stages as s (s.label)}<li>{s.label} <span class="muted">· {s.machine}</span></li>{/each}
      </ol>
      <p class="meta">"Describe each frame" means tags for what is in it, faces for how it is framed, and an image fingerprint that similar-stills and mood search compare against. The text encoder is not a step here: it only reads the phrases you type into mood search. The analyze step runs on whichever analyze worker is checked in (Jobs lists them). Color is taken from the video's own color tags. The works run one after another, in this order.</p>

      <button type="submit" class="go">Onboard {ready.length === 1 ? "it" : `all ${ready.length}`}</button>
    </form>
  {:else}
    <p class="meta" role="status">None of the chosen files can be onboarded; the reasons are below.</p>
  {/if}

  {#if left.length}
    <h2>Left out</h2>
    <ul class="list">
      {#each left as r (r.fileId)}
        <li>
          {label(r)}{#if r.identity && r.fileName}<span class="muted"> ({r.fileName})</span>{/if}:
          {#if r.existing}already onboarded (<a href={`/admin/jobs#${r.existing.chainId}`}>{r.existing.state}</a>){:else}{r.blockers.join(" ")}{/if}
        </li>
      {/each}
    </ul>
  {/if}
{/if}

<style>
  .confirm {
    margin-top: var(--s-4);
  }
  .why {
    display: block;
    color: var(--text-3);
    font-size: var(--t-meta);
  }
  .bad {
    color: var(--accent);
  }
  .muted {
    color: var(--text-3);
  }
  .more summary {
    color: var(--text-3);
    font-size: var(--t-meta);
    cursor: pointer;
  }
  .more span {
    display: block;
    font-size: var(--t-meta);
  }
  .wrap {
    overflow-wrap: anywhere;
  }
  .wid {
    width: 100%;
    min-width: 14rem;
    font-family: var(--font-mono);
  }
  .budget {
    border: 1px solid var(--line-1);
    padding: var(--s-3) var(--s-4);
    margin: var(--s-5) 0 var(--s-4);
    max-width: 720px;
  }
  .budget label {
    display: block;
    margin: var(--s-1) 0;
  }
  .sum {
    margin: var(--s-4) 0;
  }
  .list {
    margin: 0 0 var(--s-3);
    padding-left: 1.4em;
  }
  .go {
    border-color: var(--accent);
    color: var(--text-1);
    margin-top: var(--s-3);
  }
</style>
