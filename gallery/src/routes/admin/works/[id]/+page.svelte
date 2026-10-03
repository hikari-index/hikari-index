<script>
  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { formatTime, mediaUrl, placeholder } from "$lib/format.js";
  import { seasonWide } from "$lib/repeats.js";
  let { data, form } = $props();
  const pretty = (v) => String(v ?? "").replaceAll("-", " ").replaceAll("_", " ");
  const KEY = ["shot_scale", "setting", "time", "lighting"];
  const here = () => `/admin/works/${data.work.id}${data.show !== "all" ? `?show=${data.show}` : ""}`;
  const action = (name) => `${here()}${here().includes("?") ? "&" : "?"}/${name}`;
  function marks(s) {
    const parts = [];
    if (s.review === "culled") parts.push("culled");
    if (s.review === "kept") parts.push("kept");
    if (s.locked) parts.push("locked");
    if (s.excluded) parts.push("hidden");
    if (s.sensitive) parts.push("sensitive");
    if (Object.keys(s.human.facets).length || s.human.tags.add?.length || s.human.tags.remove?.length) parts.push("corrected");
    return parts;
  }
  // A still whose frame also appears in other works of the series (found
  // by matching their pools): how many, and which.
  const siblingNames = $derived(new Map(data.siblings.map((x) => [x.id, x.name])));
  function repeat(s) {
    const also = s.repeatIn.filter((id) => siblingNames.has(id));
    if (!also.length) return null;
    const others = data.siblings.length;
    const noun = data.work.entryType === "movie" ? "film" : "episode";
    const text = others === 1 ? `repeats in the other ${noun}` : `repeats in ${also.length} of ${others} other ${noun}s`;
    return { text, wide: seasonWide(also.length, others), title: `Also in ${also.map((id) => siblingNames.get(id)).join(", ")}` };
  }
  const ratio = $derived.by(() => {
    const t = data.stills.find((s) => s.tiers.length)?.tiers.at(-1);
    return t && t.w && t.h ? `${t.w} / ${t.h}` : "16 / 9";
  });
  // What the card's subtitle shows: the four framing facets when any is
  // there, else whatever else was labeled, else that nothing was.
  function subtitle(s) {
    const key = KEY.filter((f) => s.facets[f]).map((f) => pretty(s.facets[f]));
    if (key.length) return key.join(" · ");
    const other = Object.entries(s.facets).filter(([, v]) => v).map(([, v]) => pretty(v));
    return other.length ? other.slice(0, 3).join(" · ") : "no labels";
  }

  // Selection: a click on a card's box toggles it, shift-click extends
  // from the last box clicked (an opening or ending is a run of stills).
  // Kept in the page only; the selection bar posts the ids.
  let selected = $state(new Set());
  let anchor = $state(-1);
  let current = $state(-1); // the card the keys act on
  const ids = $derived(data.stills.map((s) => s.id));
  // The page component survives a walk to the next episode (same route,
  // new params), so what was selected in one must not carry into another.
  // Keyed on the id alone: an action's data refresh must not reset the
  // keyboard position.
  // The same goes for a change of view (?show=): a card ticked in one view
  // and not listed in the next would still be acted on.
  const workId = $derived(`${data.work.id} ${data.show}`);
  let shownWork = $state(null);
  $effect(() => {
    if (shownWork === workId) return;
    shownWork = workId;
    selected = new Set();
    anchor = -1;
    current = -1;
  });
  function toggle(index, shift) {
    const next = new Set(selected);
    if (shift && anchor >= 0) {
      const [a, b] = [Math.min(anchor, index), Math.max(anchor, index)];
      const on = !next.has(ids[index]);
      for (let i = a; i <= b; i++) on ? next.add(ids[i]) : next.delete(ids[i]);
    } else {
      next.has(ids[index]) ? next.delete(ids[index]) : next.add(ids[index]);
    }
    anchor = index;
    selected = next;
  }
  function selectAll(on) {
    selected = on ? new Set(ids) : new Set();
  }
  // After any action the page reloads its data in place; the selection is
  // dropped (its stills may have left this view) and the keys' card stays.
  const afterAction = () => async ({ update }) => {
    await update({ reset: false });
    selected = new Set();
  };

  // Keys, when nothing has focus that wants them: j / k move the current
  // card, space selects it, c culls or restores it, v keeps it, e edits
  // its labels, Escape clears the selection.
  let cardForms = $state({});
  function onkeydown(e) {
    const tag = e.target?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "Escape") { selected = new Set(); return; }
    if (!data.stills.length) return;
    if (e.key === "j" || e.key === "k") {
      e.preventDefault();
      current = Math.min(ids.length - 1, Math.max(0, current + (e.key === "j" ? 1 : -1)));
      document.getElementById(data.stills[current].candidate)?.scrollIntoView({ block: "nearest" });
      return;
    }
    if (current < 0) return;
    const s = data.stills[current];
    if (e.key === " ") { e.preventDefault(); toggle(current, e.shiftKey); }
    else if (e.key === "c") cardForms[s.id]?.querySelector(`button[value="${s.review === "culled" ? "unreviewed" : "culled"}"]`)?.click();
    else if (e.key === "v") cardForms[s.id]?.querySelector('button[value="kept"]')?.click();
    else if (e.key === "e") goto(`/admin/stills/${s.id}`);
  }
  // The question is asked inside the enhancement: an onsubmit handler's
  // preventDefault does not stop use:enhance, so a declined confirm there
  // still marked every still kept (measured 2026-10-02).
  const keepRestAfterAsking = ({ cancel }) => {
    if (!confirm(`Mark the ${data.counts.unreviewed} unreviewed still${data.counts.unreviewed === 1 ? "" : "s"} here as kept?\n\nCulled, hidden and already reviewed stills are not changed, and kept stills stay editable.`)) {
      cancel();
      return;
    }
    return afterAction();
  };
</script>

<svelte:window {onkeydown} />

<svelte:head>
  <title>Review · {data.work.label} — Hikari Index</title>
</svelte:head>

<nav class="crumbs"><a href={`/admin#w-${data.work.id}`}>← Review</a> {#if data.publicSheet}<a href={`/series/${data.work.id}`}>public sheet</a>{" "}{/if}<a href={`/admin/discard?scope=work&id=${data.work.id}`}>done reviewing…</a> <a href={`/admin/remove?scope=work&id=${data.work.id}`}>remove this {data.work.entryType === "movie" ? "film" : "episode"}…</a></nav>
<h1>{data.work.label}</h1>
{#if data.around?.prev || data.around?.next}
  <p class="walk">
    <span>{#if data.around.prev}<a href={`/admin/works/${data.around.prev.id}${data.show !== "all" ? `?show=${data.show}` : ""}`}>← {data.around.prev.episode ?? data.around.prev.title}{data.around.prev.episodeTitle ? ` · ${data.around.prev.episodeTitle}` : ""}</a>{/if}</span>
    <span class="fwd">{#if data.around.next}<a href={`/admin/works/${data.around.next.id}${data.show !== "all" ? `?show=${data.show}` : ""}`}>{data.around.next.episode ?? data.around.next.title}{data.around.next.episodeTitle ? ` · ${data.around.next.episodeTitle}` : ""} →</a>{/if}</span>
  </p>
{/if}
{#if data.work.entryType === "episode"}
  <details class="title-edit">
    <summary class="meta">episode title{#if data.work.episodeTitleHuman} · corrected{:else if !data.work.episodeTitleShoko} · none{/if}</summary>
    <div class="title-forms">
      <form method="POST" action={action("title")} use:enhance={afterAction}>
        <label>Title <input name="title" value={data.work.episodeTitle ?? ""} maxlength="200" placeholder="as you want it shown" /></label>
        <button type="submit" class="quiet">Save</button>
        {#if data.work.episodeTitleHuman}<button type="submit" class="quiet" name="clear" value="1">Clear the correction</button>{/if}
      </form>
      {#if data.shoko}
        <form method="POST" action={action("reread")} use:enhance={afterAction}>
          <button type="submit" class="quiet">Re-read from Shoko</button>
        </form>
      {/if}
      <span class="meta">{#if data.work.local}Typed when it was added: {data.work.episodeTitleShoko ?? "nothing"}. A title typed here stays through re-runs.{:else}Shoko says: {data.work.episodeTitleShoko ?? "nothing yet"}. A typed title stays through re-runs and re-reads.{/if}</span>
    </div>
  </details>
{/if}
<div class="top">
  <p class="filters" role="group" aria-label="Show">
    show:
    <a href={`/admin/works/${data.work.id}`} class:on={data.show === "all"}>all <span class="mono">{data.counts.all}</span></a>
    <a href={`/admin/works/${data.work.id}?show=unreviewed`} class:on={data.show === "unreviewed"}>unreviewed <span class="mono">{data.counts.unreviewed}</span></a>
    <a href={`/admin/works/${data.work.id}?show=culled`} class:on={data.show === "culled"}>culled <span class="mono">{data.counts.culled}</span></a>
    <a href={`/admin/works/${data.work.id}?show=hidden`} class:on={data.show === "hidden"}>hidden <span class="mono">{data.counts.hidden}</span></a>
    {#if data.counts.repeats}<a href={`/admin/works/${data.work.id}?show=repeats`} class:on={data.show === "repeats"} title="Stills whose frame also appears in other episodes of the season">repeats <span class="mono">{data.counts.repeats}</span></a>{/if}
    {#if data.counts.notPicked}<a href={`/admin/works/${data.work.id}?show=notpicked`} class:on={data.show === "notpicked"}>not picked <span class="mono">{data.counts.notPicked}</span></a>{/if}
    {#if data.poolLeft}<a href={`/admin/works/${data.work.id}/pool`} title="The extraction's frames that never became stills">pool <span class="mono">{data.poolLeft}</span></a>{/if}
  </p>
  {#if data.counts.unreviewed}
    <form method="POST" action={action("keepRest")} use:enhance={keepRestAfterAsking}>
      <button type="submit" class="quiet">Mark the rest kept ({data.counts.unreviewed})</button>
    </form>
  {/if}
  <span class="meta keys">keys: <kbd>j</kbd><kbd>k</kbd> move · <kbd>space</kbd> select (<kbd>shift</kbd> for a run) · <kbd>c</kbd> cull/restore · <kbd>v</kbd> keep · <kbd>e</kbd> edit labels · <kbd>esc</kbd> clear</span>
</div>
{#if form?.message}<p class="note" role="status">{form.message}</p>{/if}
{#if data.show === "repeats"}
  <p class="meta intro">Stills whose frame also appears in other {data.work.entryType === "movie" ? "films" : "episodes"} of this season, found by matching them against each other. The ones that repeat across half the season or more (openings, endings, logos) can be culled for the whole season from <a href={`/admin#w-${data.work.id}`}>Review</a>; the rest (a recap, a reused cut) are yours to judge. Nothing here is hidden or culled for you.</p>
{/if}
{#if data.show === "notpicked"}
  <p class="meta intro">Stills a re-run left out, kept because you had reviewed, locked, hidden or corrected them, and pool frames you locked that wait for a re-run. A left-out still's web images are gone with the re-run, so it shows as its color; a locked pool frame shows its preview. {#if data.discarded}This work's extra frames were discarded after review, so a re-run cannot bring them back; remove the work and onboard it again if you want them.{:else}To bring one back: <strong>lock</strong> it here, then <a href="/admin/jobs">Re-run</a> the work; the picker keeps locked frames in the set.{/if}</p>
{/if}

<div class="bar" class:on={selected.size} aria-live="polite">
  {#if selected.size}
    <form method="POST" action={action("bulk")} use:enhance={afterAction}>
      {#each [...selected] as id (id)}<input type="hidden" name="id" value={id} />{/each}
      <span class="count">{selected.size} selected</span>
      <button name="op" value="culled">cull</button>
      <button name="op" value="kept">keep</button>
      <button name="op" value="unreviewed">restore</button>
      <span class="sep"></span>
      <button name="op" value="lock">lock</button>
      <button name="op" value="unlock">unlock</button>
      <button name="op" value="hide">hide</button>
      <button name="op" value="show">show</button>
      <span class="sep"></span>
      <button type="button" class="quiet" onclick={() => selectAll(true)}>select all {ids.length}</button>
      <button type="button" class="quiet" onclick={() => selectAll(false)}>clear</button>
    </form>
  {:else}
    <span class="meta">Tick cards to act on several at once; shift-click ticks a run.</span>
  {/if}
</div>

<ol class="cards" style:--frame-ratio={ratio}>
  {#each data.stills as s, index (s.id)}
    {@const rep = repeat(s)}
    <li id={s.candidate} class:dim={s.review === "culled" || s.excluded} class:picked={selected.has(s.id)} class:current={index === current}>
      <a href={`/admin/stills/${s.id}`} aria-label={`edit the labels of the still at ${formatTime(s.ts)}`} onfocus={() => (current = index)}>
        <span class="frame">
          {#if s.tiers.length}
            <img style:background-color={placeholder(s)} src={mediaUrl(s.tiers[Math.min(1, s.tiers.length - 1)])} width={s.tiers.at(-1).w} height={s.tiers.at(-1).h} loading={index < 14 ? "eager" : "lazy"} alt={`${data.work.label}, still at ${formatTime(s.ts)}`} />
          {:else}
            <!-- a pool frame locked before any re-run: no web image yet -->
            <img src={`/admin/pool/${data.work.id}/${s.candidate}.jpg`} width="320" height="180" loading="lazy" alt={`${data.work.label}, pool frame at ${formatTime(s.ts)} (no web image yet)`} />
          {/if}
        </span>
        <span class="cap mono"><span class="fig">{index + 1}</span><span class="time">{formatTime(s.ts)}</span></span>
        <span class="sub">{subtitle(s)}</span>
      </a>
      <div class="controls">
        <label class="pick" title="select (shift-click for a run)"><input type="checkbox" checked={selected.has(s.id)} onclick={(e) => { toggle(index, e.shiftKey); current = index; }} aria-label={`select the still at ${formatTime(s.ts)}`} /></label>
        {#if marks(s).length}<span class="state">{marks(s).join(" · ")}</span>{/if}
        {#if rep}<span class="rep" class:wide={rep.wide} title={rep.title}>{rep.text}</span>{/if}
        <!-- use:enhance: the page updates in place, so a cull keeps your place. -->
        <form method="POST" action={action("review")} use:enhance={afterAction} bind:this={cardForms[s.id]}>
          <input type="hidden" name="id" value={s.id} />
          {#if s.review === "culled"}
            <button name="state" value="unreviewed">restore</button>
          {:else}
            <button name="state" value="culled">cull</button>
            {#if s.review !== "kept"}<button name="state" value="kept">keep</button>{/if}
          {/if}
        </form>
        <form method="POST" action={action("flag")} use:enhance={afterAction}>
          <input type="hidden" name="id" value={s.id} />
          <input type="hidden" name="flag" value="locked" />
          <button name="value" value={s.locked ? "0" : "1"}>{s.locked ? "unlock" : "lock"}</button>
        </form>
        <form method="POST" action={action("flag")} use:enhance={afterAction}>
          <input type="hidden" name="id" value={s.id} />
          <input type="hidden" name="flag" value="excluded" />
          <button name="value" value={s.excluded ? "0" : "1"}>{s.excluded ? "show" : "hide"}</button>
        </form>
        <a class="edit" href={`/admin/stills/${s.id}`}>edit labels</a>
      </div>
    </li>
  {/each}
</ol>

<style>
  .walk {
    display: flex;
    justify-content: space-between;
    gap: var(--s-3);
    margin: calc(-1 * var(--s-2)) 0 var(--s-2);
    font-size: var(--t-meta);
  }
  .walk a {
    color: var(--text-2);
    text-decoration: none;
  }
  .walk a:hover {
    color: var(--text-1);
  }
  .fwd {
    margin-left: auto;
    text-align: right;
  }
  .title-edit {
    margin: calc(-1 * var(--s-2)) 0 var(--s-3);
  }
  .title-edit summary {
    cursor: pointer;
    width: fit-content;
  }
  .title-forms {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--s-2) var(--s-3);
    margin-top: var(--s-2);
  }
  .title-forms form {
    display: flex;
    align-items: center;
    gap: var(--s-2);
  }
  .title-forms input {
    min-width: 18rem;
  }
  .top {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2) var(--s-4);
    align-items: center;
  }
  .top .filters {
    margin: 0;
  }
  .keys {
    color: var(--text-3);
  }
  .keys kbd {
    font: inherit;
    color: var(--text-2);
  }
  .bar {
    position: sticky;
    top: 0;
    z-index: 2;
    display: flex;
    align-items: center;
    min-height: 40px;
    margin: var(--s-2) 0;
    padding: var(--s-1) var(--s-2);
    background: var(--bg-0);
    border-bottom: 1px solid var(--line-1);
  }
  .bar.on {
    background: var(--bg-1);
    border-left: 2px solid var(--accent);
  }
  .bar form {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1) var(--s-2);
    align-items: center;
  }
  .bar .count {
    color: var(--text-1);
    margin-right: var(--s-2);
  }
  .bar .sep {
    width: 1px;
    height: 20px;
    background: var(--line-2);
    margin: 0 var(--s-1);
  }
  .cards {
    --card-min: 200px;
    gap: var(--s-4) var(--s-3);
    margin-top: var(--s-2);
  }
  .dim img {
    opacity: 0.35;
  }
  .picked {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .current {
    box-shadow: 0 0 0 1px var(--text-3);
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1);
    align-items: center;
    margin-top: var(--s-1);
    font-size: var(--t-label);
  }
  .controls form {
    display: inline;
  }
  .controls button {
    height: 24px;
    font-size: var(--t-label);
    padding: 0 6px;
  }
  .pick input {
    margin: 0 2px 0 0;
    vertical-align: middle;
  }
  .edit {
    color: var(--text-2);
    font-size: var(--t-label);
  }
  .state {
    color: var(--accent);
    font: 400 var(--t-label) / 1.4 var(--font-mono);
    width: 100%;
  }
  /* a machine's mark, not a review state: no accent */
  .rep {
    color: var(--text-3);
    font: 400 var(--t-label) / 1.4 var(--font-mono);
    width: 100%;
  }
  .rep.wide {
    color: var(--text-1);
  }
</style>
