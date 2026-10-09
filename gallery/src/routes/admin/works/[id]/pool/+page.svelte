<script>
  import { enhance } from "$app/forms";
  import { formatTime } from "$lib/format.js";
  let { data, form } = $props();
  const afterAction = () => async ({ update }) => {
    await update({ reset: false });
  };
  // 60 frames a page: each preview is made from a 2 MB master the first
  // time it is asked for, so a page is a few seconds, not a minute.
  const PAGE = 60;
  let chosen = $state(0);
  const pages = $derived(Math.max(1, Math.ceil(data.frames.length / PAGE)));
  // Clamped: locking the last frame of the last page shrinks the list.
  const page = $derived(Math.min(chosen, pages - 1));
  const shown = $derived(data.frames.slice(page * PAGE, page * PAGE + PAGE));
  // Back to the first page when the work changes (same route, new params).
  const workId = $derived(data.work.id);
  let shownWork = $state(null);
  $effect(() => {
    if (shownWork === workId) return;
    shownWork = workId;
    chosen = 0;
  });
</script>

<svelte:head>
  <title>Pool · {data.work.label} — Hikari Index</title>
</svelte:head>

<nav class="crumbs"><a href={`/admin/works/${data.work.id}`}>← {data.work.label}</a> <a href="/admin/jobs">Jobs</a></nav>
<h1>The pool · {data.work.label}</h1>
<p class="meta intro">
  The extraction kept {data.pool} frames for this work; {data.frames.length} of them never became stills. Previews are made from the masters as you scroll, so a page takes a moment the first time.
  {#if data.discarded}This work's extra frames were discarded after review and a re-run is refused after that, so frames here can be looked at but not brought in. To choose from the whole pool again, remove the work and onboard it again.{:else}To bring a frame in: <strong>lock</strong> it, then <a href="/admin/jobs">Re-run</a> the work; the picker keeps locked frames in the set, and the frame gets its labels and web images with the rest.{/if}
  {#if data.frames.some((f) => f.held)}A frame marked <strong>held</strong> sat inside a chapter that read as the opening or ending, and the pick was told to leave it; lock it to bring it in all the same.{/if}
</p>
{#if form?.message}<p class="note" role="status">{form.message}</p>{/if}

{#if pages > 1}
  <nav class="pages" aria-label="Pages">
    {#each Array(pages) as _, i (i)}
      <button type="button" class="quiet" class:on={i === page} onclick={() => (chosen = i)}>{i + 1}</button>
    {/each}
    <span class="meta">{PAGE} a page · {formatTime(shown[0]?.ts ?? 0)} to {formatTime(shown.at(-1)?.ts ?? 0)}</span>
  </nav>
{/if}

{#if !data.frames.length}
  <p class="meta">Nothing here: every frame of the pool is a still already, or this work's extraction records are missing.</p>
{/if}

<ol class="cards">
  {#each shown as f (f.candidate)}
    <li>
      <span class="frame">
        <img src={`/admin/pool/${data.work.id}/${f.candidate}.jpg`} width="320" height="180" loading="lazy" alt={`${data.work.label}, pool frame at ${formatTime(f.ts ?? 0)}`} />
      </span>
      <span class="cap mono"><span class="time">{f.ts != null ? formatTime(f.ts) : "?"}</span> <span class="src">{f.candidate}{f.held ? ` · held: ${f.held}` : f.source === "surplus" ? " · extra" : ""}</span></span>
      {#if !data.discarded}
        <div class="controls">
          <form method="POST" action="?/pin" use:enhance={afterAction}>
            <input type="hidden" name="candidate" value={f.candidate} />
            <button type="submit">lock</button>
          </form>
        </div>
      {/if}
    </li>
  {/each}
</ol>

<style>
  .intro {
    max-width: 70ch;
  }
  .pages {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1);
    align-items: center;
    margin: var(--s-3) 0;
  }
  .pages button.on {
    color: var(--text-1);
    border-color: var(--text-3);
  }
  .cards {
    --card-min: 200px;
    --frame-ratio: 16 / 9;
    gap: var(--s-4) var(--s-3);
  }
  .frame img {
    width: 100%;
    height: auto;
  }
  .cap {
    display: block;
    margin-top: var(--s-2);
    color: var(--text-2);
    font-size: var(--t-meta);
  }
  .src {
    color: var(--text-3);
  }
  .controls {
    margin-top: var(--s-1);
  }
  .controls form {
    display: inline;
  }
  .controls button {
    height: 24px;
    font-size: var(--t-label);
    padding: 0 6px;
  }
</style>
