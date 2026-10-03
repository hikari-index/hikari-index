<script>
  let { data, form } = $props();

  function size(bytes) {
    if (bytes == null) return "?";
    if (bytes >= 2 ** 30) return `${(bytes / 2 ** 30).toFixed(1)} GB`;
    if (bytes >= 2 ** 20) return `${Math.round(bytes / 2 ** 20)} MB`;
    return bytes ? `${Math.max(1, Math.round(bytes / 1024))} KB` : "0";
  }
  const p = $derived(data.preview);
  const ready = $derived(p?.works.filter((w) => !w.blocker) ?? []);
  const extra = $derived(ready.reduce((n, w) => n + (w.frames?.extra ?? 0), 0));
  const extraBytes = $derived(ready.reduce((n, w) => n + (w.frames?.extraBytes ?? 0), 0));
  const kept = $derived(ready.reduce((n, w) => n + (w.frames?.kept ?? 0), 0));
  function line(w) {
    if (w.blocker) return w.blocker;
    if (!w.frames) return "sizes unavailable";
    return `deletes ${w.frames.extra} extra frames, ${size(w.frames.extraBytes)} · leaves the ${w.frames.kept} picked frames in that folder and the bundle's masters`;
  }
  function confirmDiscard(e) {
    if (!confirm(`Discard ${extra} extra frames (${size(extraBytes)}) of ${p.label}?\n\nThe picked stills and every record stay. This cannot be undone, and the work cannot be re-run afterwards.`)) e.preventDefault();
  }
</script>

<svelte:head>
  <title>Done reviewing — Hikari Index</title>
</svelte:head>

<h1>Done reviewing: discard the extra frames</h1>
<p class="meta intro">Extraction keeps every usable frame beside the picked ones, so the pick and your review can reach them. Once an episode is reviewed they are only disk space. This deletes those extra frames; the picked stills, their full-size masters and every record stay. Afterwards the episode cannot be re-run with a different still count (it would need the frames), only removed and onboarded again.</p>

{#if form?.message}<p class="note bad" role="alert">{form.message}</p>{/if}
{#if data.done}<p class="note" role="status">Queued the discard for {data.done} {data.done === "1" ? "work" : "works"}; the source worker deletes the frames next (Jobs shows it).</p>{/if}
{#if data.asked && !p}<p class="note" role="status">That is not in the gallery.</p>{/if}

{#if p}
  <section class="confirm" aria-labelledby="what">
    <h2 id="what">{p.label}</h2>
    <table>
      <thead><tr><th>Work</th><th>What happens</th></tr></thead>
      <tbody>
        {#each p.works as w (w.id)}
          <tr class:skip={w.blocker}>
            <td><span class="work">{w.label}</span> <span class="mono meta">{w.id}</span></td>
            <td class="meta">{line(w)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    {#if ready.length}
      <p class="meta sum">{ready.length} {ready.length === 1 ? "work" : "works"} · {extra} extra frames, {size(extraBytes)} to delete · every picked still stays{p.works.length > ready.length ? ` · ${p.works.length - ready.length} skipped` : ""}</p>
      <form method="POST" action="?/discard" onsubmit={confirmDiscard}>
        <input type="hidden" name="scope" value={p.scope} />
        <input type="hidden" name="id" value={p.target} />
        <input type="hidden" name="expect" value={p.expect} />
        <label class="ok"><input type="checkbox" name="understood" value="yes" required /> Delete the extra frames. It cannot be undone; a re-run will no longer be possible.</label>
        <button type="submit" class="danger">Discard {extra} frames</button>
      </form>
    {:else}
      <p class="meta">Nothing here can be discarded now.</p>
    {/if}
  </section>
{/if}

<style>
  .intro {
    max-width: 680px;
    margin-bottom: var(--s-4);
  }
  .confirm {
    padding: var(--s-3);
    border-left: 2px solid var(--accent);
    background: var(--bg-1);
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: var(--s-2);
  }
  th,
  td {
    text-align: left;
    padding: 4px var(--s-2) 4px 0;
    vertical-align: top;
    border-bottom: 1px solid var(--line-1);
  }
  th {
    color: var(--text-3);
    font-weight: 400;
    font-size: var(--t-meta);
  }
  .skip .work {
    color: var(--text-3);
  }
  .sum {
    margin-bottom: var(--s-3);
  }
  form {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2) var(--s-3);
    align-items: center;
  }
  .ok {
    width: 100%;
  }
  .bad {
    color: var(--accent);
  }
  .danger {
    border-color: var(--accent);
  }
</style>
