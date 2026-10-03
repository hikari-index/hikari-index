<script>
  let { data } = $props();
  const gib = (b) => (b ? `${(b / 2 ** 30).toFixed(2)} GiB` : "·");
  function mediaLine(m) {
    const parts = [];
    if (m.codec) parts.push(m.codec);
    if (m.width && m.height) parts.push(`${m.width}×${m.height}`);
    if (m.bit_depth) parts.push(`${m.bit_depth}-bit`);
    if (m.duration_seconds) parts.push(`${Math.round(m.duration_seconds / 60)} min`);
    return parts.join(" · ");
  }
  const title = $derived(data.series ? `Onboard · ${data.series.name} — Hikari Index` : "Onboard — Hikari Index");

  const files = $derived(data.episodes.flatMap((e) => e.files));
  const readyIds = $derived(files.filter((f) => f.ready).map((f) => f.id));
  let chosen = $state([]);
  const picked = $derived(files.filter((f) => chosen.includes(f.id)));
  const allReady = $derived(readyIds.length > 0 && readyIds.every((id) => chosen.includes(id)));
  function toggleReady() {
    chosen = allReady ? chosen.filter((id) => !readyIds.includes(id)) : [...new Set([...chosen, ...readyIds])];
  }
  // A season bigger than one batch: take the next batch of ready files
  // that are not chosen yet, in episode order.
  const nextBatch = $derived(readyIds.filter((id) => !chosen.includes(id)).slice(0, data.batchMax));
  function chooseNext() {
    chosen = [...chosen, ...nextBatch];
  }
  // One episode, several files: ticking one unticks the others, so two
  // releases of the same episode cannot go in by accident.
  function onePerEpisode(e, f) {
    if (!chosen.includes(f.id)) return;
    const others = e.files.filter((x) => x.id !== f.id).map((x) => x.id);
    chosen = chosen.filter((id) => !others.includes(id));
  }
  const sum = $derived.by(() => {
    const bytes = picked.reduce((t, f) => t + (f.size || 0), 0);
    const minutes = picked.reduce((t, f) => t + (f.media.duration_seconds || 0) / 60, 0);
    const e = data.estimate;
    let time = null;
    if (e && minutes) {
      const m = Math.max(1, Math.round((e.secondsPerMinute * minutes) / 60));
      time = m < 90 ? `${m} min` : `${(m / 60).toFixed(1)} h`;
    }
    return { bytes, minutes, time, stills: e && minutes ? Math.round(e.stillsPerMinute * minutes) : null };
  });
  const status = (f) => (f.work ? "in the gallery" : f.chain ? `onboarding: ${f.chain.state.replaceAll("_", " ")}` : f.blockers.length ? (f.blockers.some((b) => b.startsWith("Shoko's record")) ? "stale record" : "needs a human") : f.ready ? "ready" : "choose a file");
</script>

<svelte:head>
  <title>{title}</title>
</svelte:head>

<nav class="crumbs"><a href="/admin/onboard">← Onboard</a></nav>

{#if data.error}
  <p class="note" role="alert">{data.error}</p>
{:else}
  <h1>{data.series.name}</h1>
  <p class="meta">{[data.series.type, data.series.aired].filter(Boolean).join(" · ")}{data.hidden ? ` · ${data.hidden} credits, trailers and other extras not listed` : ""}</p>

  {#if !data.episodes.length}
    <p class="meta" role="status">Shoko lists no episodes with files for this series.</p>
  {:else}
    <form method="GET" action="/admin/onboard/confirm">
      <div class="tools">
        {#if readyIds.length}
          {#if readyIds.length > data.batchMax}
            <button type="button" onclick={chooseNext} disabled={!nextBatch.length || picked.length + nextBatch.length > data.batchMax}>Select the next {nextBatch.length} ready</button>
            <span class="meta">{readyIds.length} ready, at most {data.batchMax} per batch</span>
          {:else}
            <button type="button" onclick={toggleReady}>{allReady ? "Clear the ready ones" : `Select all ready (${readyIds.length})`}</button>
          {/if}
        {/if}
        <span class="meta">An episode with several files is never chosen for you: tick the one you want.</span>
      </div>
      <table class="data">
        <thead>
          <tr><th scope="col"><span class="sr">Choose</span></th><th scope="col">Episode</th><th scope="col">File</th><th scope="col" class="text">Status</th></tr>
        </thead>
        <tbody>
          {#each data.episodes as e (e.id)}
            {#each e.files.length ? e.files : [null] as f, i (f?.id ?? `none-${e.id}`)}
              <tr class:off={f && !f.open}>
                <td class="pick">
                  {#if f?.open}
                    <input type="checkbox" name="file" value={f.id} bind:group={chosen} onchange={() => onePerEpisode(e, f)} aria-label={`Choose ${e.type === "Special" ? "S" : "E"}${e.number ?? "?"}${e.files.length > 1 ? `, file ${i + 1}${f.name ? ` (${f.name})` : ""}` : ""}`} />
                  {/if}
                </td>
                {#if i === 0}
                  <th scope="row" rowspan={Math.max(1, e.files.length)}>
                    <span class="mono">{e.type === "Special" ? "S" : "E"}{e.number ?? "?"}</span>
                    {e.title}
                    {#if e.aired}<span class="aired">{e.aired}</span>{/if}
                  </th>
                {/if}
                {#if f}
                  <td class="text">
                    <span class="mono">{gib(f.size)}</span> · {mediaLine(f.media)}
                    {#if f.variation}<span class="flag">variation</span>{/if}
                    {#if f.name && (e.files.length > 1 || f.blockers.length)}<span class="fname mono">{f.name}</span>{/if}
                    {#each f.blockers as b (b)}<span class="why">{b}</span>{/each}
                  </td>
                  <td class="text">
                    {#if f.work}
                      <a href={`/admin/works/${f.work}`}>in the gallery</a>
                    {:else if f.chain}
                      <a href={`/admin/jobs#${f.chain.chainId}`}>{status(f)}</a>
                    {:else}
                      <span class:muted={!f.ready}>{status(f)}</span>
                    {/if}
                  </td>
                {:else}
                  <td class="text muted" colspan="2">no file</td>
                {/if}
              </tr>
            {/each}
          {/each}
        </tbody>
      </table>

      <div class="bar" aria-live="polite">
        {#if picked.length}
          <span>
            <strong>{picked.length} chosen</strong>
            · reads {gib(sum.bytes)} ({Math.round(sum.minutes)} min of video)
            {#if sum.time}· about {sum.time} of machine time · about {sum.stills} stills at balanced{/if}
          </span>
          {#if picked.length > data.batchMax}
            <span class="why">At most {data.batchMax} at once; untick some.</span>
          {:else}
            <button type="submit" class="go">Review and onboard {picked.length}</button>
          {/if}
        {:else}
          <span class="muted">Tick episodes to onboard them together.</span>
        {/if}
      </div>
    </form>
  {/if}
{/if}

<style>
  .fname {
    display: block;
    color: var(--text-3);
    font-size: var(--t-meta);
    overflow-wrap: anywhere;
  }
  .tools {
    display: flex;
    align-items: baseline;
    gap: var(--s-4);
    margin: var(--s-3) 0;
  }
  .pick {
    width: 2rem;
  }
  .off {
    color: var(--text-3);
  }
  .aired {
    color: var(--text-3);
    font-size: var(--t-meta);
    margin-left: var(--s-2);
  }
  .flag {
    color: var(--accent);
    margin-left: var(--s-2);
  }
  .why {
    display: block;
    color: var(--text-3);
    font-size: var(--t-meta);
  }
  .muted {
    color: var(--text-3);
  }
  .bar {
    position: sticky;
    bottom: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--s-2) var(--s-4);
    margin-top: var(--s-4);
    padding: var(--s-3) var(--s-4);
    background: var(--bg-1);
    border-top: 1px solid var(--line-1);
  }
  .go {
    margin-left: auto;
    border-color: var(--accent);
    color: var(--text-1);
  }
</style>
