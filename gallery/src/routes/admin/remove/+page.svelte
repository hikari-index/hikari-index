<script>
  let { data, form } = $props();

  const when = (t) => new Date(t).toLocaleString("en-US", { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  function size(bytes) {
    if (bytes == null) return "?";
    if (bytes >= 2 ** 30) return `${(bytes / 2 ** 30).toFixed(1)} GB`;
    if (bytes >= 2 ** 20) return `${Math.round(bytes / 2 ** 20)} MB`;
    return bytes ? `${Math.max(1, Math.round(bytes / 1024))} KB` : "0";
  }
  const SCOPE = { work: "episode", season: "season", franchise: "title" };
  const p = $derived(data.preview);
  const held = $derived(p?.works.filter((w) => w.held) ?? []);
  const total = $derived(
    p ? p.works.reduce((t, w) => t + (w.disk?.run?.bytes ?? 0) + (w.disk?.images?.bytes ?? 0), 0) : 0,
  );
  const stills = $derived(p ? p.works.reduce((n, w) => n + w.stills, 0) : 0);
  const reviewed = $derived(p ? p.works.reduce((n, w) => n + w.reviewed, 0) : 0);
  const corrected = $derived(p ? p.works.reduce((n, w) => n + w.corrected, 0) : 0);
  const done = $derived(data.done ? data.removals.find((r) => r.id === data.done) : null);

  function disk(w) {
    const run = w.disk?.run;
    const img = w.disk?.images;
    const parts = [];
    if (run?.bytes) parts.push(`run folder ${size(run.bytes)} (${run.files} files)`);
    if (img?.bytes) parts.push(`web images ${size(img.bytes)}`);
    if (!w.disk) return "sizes unavailable";
    return parts.join(" · ") || "nothing on disk";
  }
  function status(r) {
    const s = r.scrubs;
    if (!s.length) return "";
    const bytes = s.reduce((t, x) => t + (x.bytes ?? 0), 0);
    const doneN = s.filter((x) => x.state === "committed").length;
    const bad = s.filter((x) => ["dead_letter", "blocked", "ineligible"].includes(x.state));
    if (bad.length) return `files: ${bad.length} of ${s.length} could not be deleted; see Jobs`;
    if (s.every((x) => x.state === "cancelled")) return "files kept (deletion cancelled)";
    if (doneN === s.length) return bytes ? `files deleted, ${size(bytes)}` : "done; nothing was on disk";
    return `files: ${doneN} of ${s.length} deleted, the rest waiting for the source worker`;
  }
  function confirmRemove(e) {
    if (!confirm(`Remove ${p.label}?\n\n${p.works.length} ${p.works.length === 1 ? "work" : "works"}, ${stills} stills and ${size(total)} on disk. This cannot be undone.`)) e.preventDefault();
  }
</script>

<svelte:head>
  <title>{data.asked ? "Remove" : "Removed"} — Hikari Index</title>
</svelte:head>

{#if data.asked}
  <h1>Remove from the index</h1>
  <p class="meta intro">Takes an episode, a season or a whole title out of the gallery and deletes what the pipeline made for it: its stills with your review marks and corrections, its job history, its web images and its run folder (masters, extra frames and records). A record of the removal stays here. To bring it back, onboard it again; the pipeline runs from the start.</p>
{:else}
  <h1>Removed</h1>
  <p class="meta intro">What has been taken out of the index, newest first. Nothing is removed from this page: use <strong>remove…</strong> on a title or season in <a href="/admin">Review</a>, on an episode's workbench, or on a row in <a href="/admin/jobs">Jobs</a>. To bring something back, onboard it again; the pipeline runs from the start.</p>
{/if}

{#if form?.message}<p class="note bad" role="alert">{form.message}</p>{/if}
{#if done}
  <p class="note" role="status">Removed {done.label}: {done.works} {done.works === 1 ? "work" : "works"}, {done.stills} stills. {status(done)}.</p>
{/if}

{#if data.asked && !p}
  <p class="note" role="status">That is not in the index (any more). Past removals are listed below.</p>
{/if}

{#if p}
  <section class="confirm" aria-labelledby="what">
    <h2 id="what">Remove the {SCOPE[p.scope]} {p.label}</h2>
    <table>
      <thead><tr><th>Work</th><th>Stills</th><th>On disk</th></tr></thead>
      <tbody>
        {#each p.works as w (w.id)}
          <tr class:held={w.held}>
            <td><span class="work">{w.label}</span> <span class="mono meta">{w.id}</span>{#if !w.imported}<span class="meta"> · not in the gallery (its onboarding did not finish)</span>{/if}{#if w.held}<span class="bad"> · a worker is running a stage of it</span>{/if}</td>
            <td>{w.stills}{#if w.reviewed || w.corrected}<span class="meta"> · {w.reviewed} reviewed{w.corrected ? `, ${w.corrected} corrected` : ""}</span>{/if}</td>
            <td class="meta">{disk(w)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    <p class="meta sum">{p.works.length} {p.works.length === 1 ? "work" : "works"} · {stills} stills{reviewed ? ` (${reviewed} reviewed${corrected ? `, ${corrected} corrected` : ""})` : ""} · {total ? `${size(total)} on disk` : "nothing on disk"}</p>

    {#if held.length}
      <p class="note bad">A worker is running a stage of {held.map((w) => w.label).join(", ")}. Cancel it on <a href="/admin/jobs">Jobs</a> and wait for it to stop, then come back.</p>
    {:else}
      <form method="POST" action="?/remove" onsubmit={confirmRemove}>
        <input type="hidden" name="scope" value={p.scope} />
        <input type="hidden" name="id" value={p.target} />
        <input type="hidden" name="expect" value={p.expect} />
        <label class="reason">Why (optional, kept with the record) <input name="reason" maxlength="500" placeholder="e.g. damaged source file, onboard again" /></label>
        <label class="ok"><input type="checkbox" name="understood" value="yes" required /> Delete all of this. It cannot be undone; onboarding again re-runs the pipeline.</label>
        <button type="submit" class="danger">Remove {p.works.length === 1 ? "it" : `all ${p.works.length}`}</button>
        <span class="meta">The stills leave the gallery at once; the source worker deletes the files next (Jobs shows it).</span>
      </form>
    {/if}
  </section>
{/if}

<section aria-label="Past removals">
  {#if data.asked}<h2>Removed before</h2>{/if}
  {#if !data.removals.length}
    <p class="meta">Nothing has been removed.</p>
  {:else}
    <table class="past">
      <thead><tr><th>When</th><th>What</th><th>Why</th><th>Status</th></tr></thead>
      <tbody>
        {#each data.removals as r (r.id)}
          <tr>
            <td class="meta nowrap">{when(r.createdAt)}</td>
            <td>{r.label} <span class="meta">· {SCOPE[r.scope]} · {r.works} {r.works === 1 ? "work" : "works"}, {r.stills} stills</span></td>
            <td class="meta">{r.reason ?? ""}</td>
            <td class="meta">{status(r)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
</section>

<style>
  .intro {
    max-width: 680px;
    margin-bottom: var(--s-4);
  }
  .confirm {
    margin-bottom: var(--s-5);
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
  .held .work {
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
  .reason {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2);
    align-items: center;
    width: 100%;
  }
  .reason input {
    width: 28rem;
    max-width: 100%;
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
  .nowrap {
    white-space: nowrap;
  }
  @media (max-width: 700px) {
    .past th:nth-child(3),
    .past td:nth-child(3) {
      display: none;
    }
  }
</style>
