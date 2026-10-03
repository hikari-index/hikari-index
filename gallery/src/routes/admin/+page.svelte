<script>
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { tick } from "svelte";
  // Back from a workbench (its "← Review" link carries #w-<id>): open that
  // title, land on the row, and mark it, instead of the top of the tree.
  // Keyed on the page's hash, so a hash-only move (Back/Forward between
  // two rows) does the same; the router keeps page.url current for those.
  $effect(() => {
    const hash = page.url.hash;
    tick().then(() => {
      for (const el of document.querySelectorAll(".row.here")) el.classList.remove("here");
      const id = decodeURIComponent(hash.slice(1));
      const row = id && document.getElementById(id);
      if (!row) return;
      row.closest("details")?.setAttribute("open", "");
      row.classList.add("here");
      row.scrollIntoView({ block: "center" });
    });
  });
  // Keep and undo update the page in place (no reload, hash and open
  // titles kept). The confirm is asked here, inside the enhancement:
  // a declined confirm cancels the submission (an onsubmit
  // preventDefault would not stop use:enhance).
  // One at a time: a second answer would replace the first one's message
  // and with it the only Undo for that batch.
  let pending = false;
  const once = (question) => ({ cancel }) => {
    if (pending || (question && !confirm(question))) {
      cancel();
      return;
    }
    pending = true;
    return async ({ update }) => {
      try {
        await update({ reset: false });
      } finally {
        pending = false;
      }
    };
  };
  const keepInPlace = (n, label) => once(`Mark the ${n} unreviewed still${n === 1 ? "" : "s"} in ${label} as kept?\n\nCulled, hidden and already reviewed stills are not changed, kept stills stay editable, and you can undo right after.`);
  const cullInPlace = (n, label) => once(`Cull the ${n} unreviewed still${n === 1 ? "" : "s"} in ${label} that repeat in at least half of its other episodes, and in at least two (openings, endings, logos)?\n\nLocked, kept, hidden and already culled stills are not changed, and you can undo right after. To keep one, lock or keep it first.`);
  const inPlace = once(null);
  let { data, form } = $props();

  let q = $state("");
  const norm = (s) => (s ?? "").toLowerCase();
  const matches = (text) => !q.trim() || norm(text).includes(norm(q.trim()));

  // "Needs a look first": anything with stills left to review, newest import
  // first; the rest after, by name. A-Z is Shoko's sort name.
  const byName = (a, b) => (a.sortName ?? a.name).localeCompare(b.sortName ?? b.name);
  const byAttention = (a, b) =>
    (b.unreviewed > 0) - (a.unreviewed > 0) || (b.unreviewed > 0 ? (b.importedAt ?? "").localeCompare(a.importedAt ?? "") : 0) || byName(a, b);

  // A franchise shows when its name, a season or an episode matches; a
  // matching franchise shows everything under it.
  const shown = $derived(
    data.franchises
      .map((f) => {
        if (matches(f.name)) return f;
        const seasons = f.seasons
          .map((s) => {
            if (matches(s.name)) return s;
            const works = s.works.filter((w) => matches(w.label));
            // A season shown in part keeps its own totals but loses its
            // season-wide button: that would act on episodes not on screen.
            return { ...s, works, of: works.length < s.works.length ? s.works.length : null };
          })
          .filter((s) => s.works.length);
        return seasons.length ? { ...f, seasons } : null;
      })
      .filter(Boolean)
      .toSorted(data.order === "az" ? byName : byAttention),
  );
  const loose = $derived(data.loose.filter((w) => matches(w.label)));

  const reviewedShare = (x) => (x.picked ? Math.round((100 * (x.picked - x.unreviewed)) / x.picked) : 100);
  function counts(x) {
    const parts = [`${x.picked} picked`];
    parts.push(x.unreviewed ? `${x.unreviewed} to review` : "all reviewed");
    if (x.repeats) parts.push(`${x.repeats} repeat${x.repeats === 1 ? "" : "s"}`);
    if (x.culled) parts.push(`${x.culled} culled`);
    if (x.hidden) parts.push(`${x.hidden} hidden`);
    if (x.corrected) parts.push(`${x.corrected} corrected`);
    return parts.join(" · ");
  }
  const episodeName = (w) => [w.episode, w.episodeTitle].filter(Boolean).join(" · ") || w.label;
  // Once an episode is reviewed, its extra frames can go (ADR-0009).
  const discard = (w) => data.discards[w.id] ?? null;
  const discardNote = (w) => {
    const d = discard(w);
    if (!d) return null;
    if (d.state === "committed") return `extras discarded${d.bytes ? `, ${Math.round(d.bytes / 2 ** 20)} MB` : ""}`;
    if (["blocked", "dead_letter", "cancelled"].includes(d.state)) return "discard did not finish (Jobs)";
    return "discarding extras";
  };
  const action = (name) => `?${data.order === "az" ? "order=az&" : ""}/${name}`;
  // A season whose episodes are not all matched against each other yet has
  // no "Cull the repeats": its marks may still change.
  const due = $derived(new Set(data.repeats.due));
  const checking = (s) => s.works.length > 1 && s.works.some((w) => due.has(w.id));
  // The top line's word on it: works still to match, and works whose
  // records could not be read (those are tried again every ten minutes).
  const repeatsNote = $derived.by(() => {
    const works = (n) => `${n} ${n === 1 ? "work" : "works"}`;
    const unread = Object.keys(data.repeats.failed).length;
    const waiting = data.repeats.due.length - unread;
    return [waiting ? `checking ${works(waiting)} for repeats` : null, unread ? `repeats not checked for ${works(unread)} (records unreadable)` : null].filter(Boolean).join(" · ");
  });
  // Why a season's check cannot finish (its run records could not be read).
  const unchecked = (s) => s.works.map((w) => data.repeats.failed[w.id] && `${w.id}: ${data.repeats.failed[w.id]}`).find(Boolean) ?? null;
</script>

<svelte:head>
  <title>Review — Hikari Index</title>
</svelte:head>

<h1>Review</h1>
<p class="meta intro">Grouped the way the Library is. A season's episodes are matched against each other, so its openings, endings and logos can be culled in one go (<em>Cull the repeats</em>; lock or keep a still first to hold on to it). Cull whatever else should go on each episode's sheet, then mark the rest as kept for the episode or the whole season. Kept stills stay editable.</p>

{#if form?.message}
  <div class="note" role="status">
    <span>{form.message}</span>
    {#if form.undo}
      <form method="POST" action={action("undo")} use:enhance={inPlace}>
        <input type="hidden" name="what" value={form.undo.what} />
        <input type="hidden" name="kind" value={form.undo.kind} />
        <input type="hidden" name="id" value={form.undo.id} />
        <input type="hidden" name="at" value={form.undo.at} />
        <input type="hidden" name="label" value={form.undo.label} />
        <button type="submit">Undo</button>
      </form>
    {/if}
  </div>
{/if}

<div class="top">
  {#if data.next}
    <a class="continue" href={`/admin/works/${data.next.work.id}?show=unreviewed#${data.next.id.split("/")[1]}`}>Continue reviewing</a>
    <span class="meta">next: {data.next.work.label} (its sheet, unreviewed only) · {data.totals.unreviewed} to review in all{#if data.worthALook}{" · "}<a href="/admin/exceptions">{data.worthALook} worth a look first</a>{/if}{#if data.repeats.due.length}{" · "}{repeatsNote}{/if}</span>
  {:else}
    <span class="meta">Nothing left to review{#if data.repeats.due.length}{" · "}{repeatsNote}{/if}.</span>
  {/if}
</div>

<div class="tools">
  <label class="find"><span class="sr">Find a title</span><input type="search" placeholder="Find a title or episode" bind:value={q} /></label>
  <p class="filters" role="group" aria-label="Order">
    <a href="/admin" class:on={data.order === "attention"}>needs a look first</a>
    <a href="/admin?order=az" class:on={data.order === "az"}>a–z</a>
  </p>
</div>

{#snippet bar(x)}
  <span class="bar" title={`${reviewedShare(x)}% reviewed`}><span style:width={`${reviewedShare(x)}%`}></span></span>
{/snippet}

{#snippet keep(kind, id, n, label)}
  {#if n > 0}
    <form method="POST" action={action("keep")} use:enhance={keepInPlace(n, label)}>
      <input type="hidden" name="kind" value={kind} />
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="label" value={label} />
      <button type="submit" class="quiet">Mark the rest kept ({n})</button>
    </form>
  {/if}
{/snippet}

{#snippet cull(s)}
  {#if checking(s) && unchecked(s)}
    <span class="meta" title={unchecked(s)}>repeats not checked (records unreadable)</span>
  {:else if checking(s)}
    <span class="meta" title="Its episodes are being matched against each other; the button appears when that is done">checking for repeats…</span>
  {:else if s.repeats > 0}
    <form method="POST" action={action("cull")} use:enhance={cullInPlace(s.repeats, s.name)}>
      <input type="hidden" name="kind" value="season" />
      <input type="hidden" name="id" value={s.id} />
      <input type="hidden" name="label" value={s.name} />
      <button type="submit" class="quiet">Cull the repeats ({s.repeats})</button>
    </form>
  {/if}
{/snippet}

{#snippet episodes(works, prefix)}
  <ul class="eps">
    {#each works as w (w.id)}
      <li class="row" id={`w-${w.id}`}>
        <a class="name" href={`/admin/works/${w.id}`}>{prefix ? w.label : episodeName(w)}</a>
        <span class="meta">{counts(w)}{#if discardNote(w)}{" · "}{discardNote(w)}{/if}</span>
        {@render bar(w)}
        <span class="act">{@render keep("work", w.id, w.unreviewed, w.label)}{#if !w.unreviewed && !discard(w)}<a class="quiet" href={`/admin/discard?scope=work&id=${w.id}`} title="Delete this episode's extra frames; the picked stills and all records stay">done reviewing…</a>{/if}</span>
      </li>
    {/each}
  </ul>
{/snippet}

{#if !shown.length && !loose.length}
  <p class="meta">{q ? "Nothing matches that." : "Nothing in the gallery yet. New work starts from Onboard."}</p>
{/if}

{#each shown as f (f.id)}
  <details class="fr" open={q.trim() ? true : undefined}>
    <summary class="row">
      <span class="name">{f.name}</span>
      <span class="meta">{f.seasons.length > 1 ? `${f.seasons.length} seasons · ` : ""}{counts(f)}</span>
      {@render bar(f)}
      <span class="act">{#if f.seasons.length === 1 && !f.seasons[0].of}{@render cull(f.seasons[0])}{@render keep("season", f.seasons[0].id, f.seasons[0].unreviewed, f.seasons[0].name)}{#if !f.seasons[0].unreviewed && f.seasons[0].works.some((w) => !discard(w))}<a class="quiet" href={`/admin/discard?scope=season&id=${f.seasons[0].id}`}>done reviewing…</a>{/if}{/if}<a class="quiet" href={`/admin/remove?scope=franchise&id=${f.id}`}>remove…</a></span>
    </summary>
    {#each f.seasons as s (s.id)}
      <div class="season" class:solo={f.seasons.length === 1}>
        <div class="row" class:sr={f.seasons.length === 1}>
          <span class="name">{s.name}</span>
          <span class="meta">{s.of ? `${s.works.length} of ${s.of} entries match` : `${s.works.length} ${s.works.length === 1 ? "entry" : "entries"} · ${counts(s)}`}</span>
          {@render bar(s)}
          <!-- A single-season title's row is off screen (.sr) and its actions are on the title's row: no second, unseen set here. -->
          {#if f.seasons.length > 1}<span class="act">{#if s.works.length > 1 && !s.of}{@render cull(s)}{@render keep("season", s.id, s.unreviewed, s.name)}{/if}{#if !s.of && !s.unreviewed && s.works.some((w) => !discard(w))}<a class="quiet" href={`/admin/discard?scope=season&id=${s.id}`}>done reviewing…</a>{/if}{#if !s.of}<a class="quiet" href={`/admin/remove?scope=season&id=${s.id}`}>remove…</a>{/if}</span>{/if}
        </div>
        {@render episodes(s.works, false)}
      </div>
    {/each}
  </details>
{/each}

{#if loose.length}
  <h2>Not grouped</h2>
  <p class="meta">Works whose Shoko series is not synced into the Library yet.</p>
  {@render episodes(loose, true)}
{/if}

<style>
  .intro {
    max-width: 680px;
    margin-bottom: var(--s-4);
  }
  .note {
    display: flex;
    gap: var(--s-3);
    align-items: baseline;
  }
  .top {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-3);
    align-items: baseline;
    margin-bottom: var(--s-3);
  }
  .continue {
    padding: 6px var(--s-3);
    border: 1px solid var(--accent);
    color: var(--text-1);
  }
  .tools {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2) var(--s-4);
    align-items: center;
    margin-bottom: var(--s-3);
  }
  .find input {
    width: 20rem;
    max-width: 100%;
  }
  .tools .filters {
    margin: 0;
  }
  .row {
    display: grid;
    grid-template-columns: minmax(0, 18rem) minmax(0, 1fr) 8rem 12rem;
    gap: var(--s-1) var(--s-3);
    align-items: center;
    padding: 6px 0;
  }
  .fr {
    border-top: 1px solid var(--line-1);
  }
  .fr > summary {
    cursor: pointer;
    list-style: none;
  }
  .fr > summary::-webkit-details-marker {
    display: none;
  }
  .fr > summary .name::before {
    content: "▸ ";
    color: var(--text-3);
  }
  .fr[open] > summary .name::before {
    content: "▾ ";
  }
  .fr > summary .name {
    color: var(--text-1);
    font-weight: 500;
  }
  .season {
    margin: 0 0 var(--s-2) var(--s-4);
  }
  .season.solo {
    margin-left: 0;
  }
  .season > .row .name {
    color: var(--text-2);
  }
  .eps {
    list-style: none;
    margin: 0 0 0 var(--s-4);
    padding: 0;
  }
  .eps .row {
    padding: 3px 0;
  }
  .name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row .meta {
    font-size: var(--t-meta);
  }
  .bar {
    display: block;
    height: 4px;
    background: var(--line-1);
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--text-3);
  }
  .act {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1) var(--s-3);
    align-items: center;
    justify-content: flex-end;
  }
  .act button,
  .act a {
    white-space: nowrap;
  }
  /* the row the workbench sent you back to (class set on arrival) */
  :global(.row.here .name) {
    color: var(--accent);
  }
  .quiet {
    color: var(--text-3);
  }
  /* room for a season's three actions on one line, where there is room */
  @media (min-width: 1200px) {
    .row {
      grid-template-columns: minmax(0, 18rem) minmax(0, 1fr) 8rem 24rem;
    }
  }
  @media (max-width: 900px) {
    .row {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    .row .meta {
      grid-column: 1 / -1;
      order: 3;
    }
    .bar {
      grid-column: 1 / -1;
      order: 4;
    }
  }
</style>
