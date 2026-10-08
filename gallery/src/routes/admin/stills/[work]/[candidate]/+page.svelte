<script>
  import { beforeNavigate, goto } from "$app/navigation";
  import { formatTime, mediaUrl, placeholder } from "$lib/format.js";
  import Swatches from "$lib/Swatches.svelte";
  let { data, form } = $props();
  const s = $derived(data.still);
  const pretty = (v) => String(v ?? "").replaceAll("-", " ").replaceAll("_", " ");
  const detail = $derived(s.tiers[s.tiers.length - 1]);
  // Tags currently shown: machine minus removed, plus added.
  const shownTags = $derived(s.tags);
  const machineTags = $derived(s.machine.tags);
  const addedTags = $derived(s.human.tags.add ?? []);

  // A save that was refused comes back with what was typed, so the form
  // shows the attempt, not the saved still.
  const v = $derived(form?.values ?? null);
  const facetValue = (family, human) => v ? (v[`facet:${family}`] ?? "__proposed") : human === undefined ? "__proposed" : human === null ? "__none" : human;
  const tagOn = (t) => (v ? (v.tags ?? []).includes(t) : shownTags.includes(t));
  const st = $derived(v ? v.state : s.review);
  // The "labels checked" mark holds only for the labels it was given on: a
  // re-run that changed them leaves it unticked until checked again.
  const sameLabels = (a, b) => {
    const x = Object.entries(a ?? {}).filter(([, val]) => val);
    const y = Object.entries(b ?? {}).filter(([, val]) => val);
    return x.length === y.length && x.every(([k, val]) => (b ?? {})[k] === val);
  };
  // Suggestions (#41) count as labels here: a changed suggestion makes the mark stale too.
  const suggestedValues = (x) => Object.fromEntries(Object.entries(x ?? {}).map(([k, val]) => [k, val?.value]));
  const checkStale = $derived(!!s.labelCheck && !(sameLabels(s.labelCheck.machine, s.machine.facets) && sameLabels(suggestedValues(s.labelCheck.suggested), suggestedValues(s.suggested))));
  const labelsChecked = $derived(v ? v.labels_checked === "on" : !!s.labelCheck && !checkStale);
  // Changing a label means it was looked at: tick the mark. On input, not
  // change, so a correction typed and saved with Ctrl+Enter still ticks it.
  function labelEdited(e) {
    const box = e.currentTarget.querySelector('input[name="labels_checked"]');
    if (box && e.target !== box && String(e.target?.name ?? "").startsWith("facet:")) box.checked = true;
  }
  // Which machine labels this page shows; a checked save is refused if a
  // re-run changed them since (the mark would certify labels never seen).
  const labelsSeen = $derived(JSON.stringify([Object.entries(s.machine.facets).sort(), Object.entries(s.suggested ?? {}).map(([k, x]) => [k, x.value]).sort()]));

  // Unsaved edits are guarded on every way out: the arrow keys, the links,
  // the browser's own back and close. A submit clears the guard first.
  let dirty = $state(false);
  let leaving = $state(false);
  // Moving to another still reuses this page: start its guard afresh.
  $effect.pre(() => {
    s.id;
    dirty = false;
    leaving = false;
  });
  beforeNavigate(({ cancel }) => {
    if (dirty && !leaving && !confirm("You have unsaved label changes here. Leave without saving?")) cancel();
  });
  function onbeforeunload(e) {
    if (dirty && !leaving) e.preventDefault();
  }

  // Keyboard: ← → walk the episode, n jumps to the next unreviewed, and
  // Ctrl+Enter (Cmd+Enter) saves and moves on. Arrows are left alone while
  // a field has focus, so selects and text inputs keep theirs.
  let saveNext;
  function onkeydown(e) {
    const tag = e.target?.tagName;
    const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      saveNext?.click();
      return;
    }
    if (typing || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "ArrowLeft" && data.walk.prev) goto(`/admin/stills/${data.walk.prev.id}`);
    else if (e.key === "ArrowRight" && data.walk.next) goto(`/admin/stills/${data.walk.next.id}`);
    else if (e.key === "n" && data.walk.nextUnreviewed) goto(`/admin/stills/${data.walk.nextUnreviewed.id}`);
  }
</script>

<svelte:window {onkeydown} {onbeforeunload} />

<svelte:head>
  <title>Edit · {data.work?.label ?? s.workId} at {formatTime(s.ts)} — Hikari Index</title>
</svelte:head>

<nav class="crumbs">
  <a href={`/admin/works/${s.workId}#${s.candidate}`}>← {data.work?.label ?? s.workId}</a>
  {#if detail}
    <a href={`/still/${s.id}`}>public page</a>
    <a href={`/admin/stills/${s.id}/card`}>make a post card</a>
  {/if}
</nav>
<h1>{data.work?.label ?? s.workId} · <span class="mono">{formatTime(s.ts) || s.candidate}</span></h1>
{#snippet walk()}
  <nav class="walk mono" aria-label="Move through the episode">
    <span>{#if data.walk.prev}<kbd aria-hidden="true">←</kbd> <a href={`/admin/stills/${data.walk.prev.id}`}>{formatTime(data.walk.prev.ts)}</a>{/if}</span>
    <span class="pos">{data.walk.index} of {data.walk.total} (culled and hidden included) · {data.walk.unreviewedLeft} unreviewed left{#if s.review !== "unreviewed"}{" · "}this one: {s.review}{/if}</span>
    <span class="fwd">
      {#if data.walk.nextUnreviewed && data.walk.nextUnreviewed.id !== data.walk.next?.id}<a href={`/admin/stills/${data.walk.nextUnreviewed.id}`}>next unreviewed ↷</a> <kbd aria-hidden="true">n</kbd>{/if}
      {#if data.walk.next}<a href={`/admin/stills/${data.walk.next.id}`}>{formatTime(data.walk.next.ts)}</a> <kbd aria-hidden="true">→</kbd>{/if}
    </span>
  </nav>
{/snippet}
{@render walk()}

<div class="editor">
  <div class="pic">
    {#if detail}
      <img style:background-color={placeholder(s)} src={mediaUrl(detail)} width={detail.w} height={detail.h} alt={`${data.work?.label ?? s.workId}, still at ${formatTime(s.ts)}`} />
    {:else}
      <!-- a pool frame locked before any re-run: no web image, no labels yet -->
      <img src={`/admin/pool/${s.workId}/${s.candidate}.jpg`} width="320" height="180" alt={`${data.work?.label ?? s.workId}, pool frame at ${formatTime(s.ts)} (no web image yet)`} />
      <p class="meta">A locked pool frame: it gets its labels and web images when the work is re-run.</p>
    {/if}
    <Swatches palette={s.palette} height="10px" />
  </div>

  {#key s.id}
  <form method="POST" action="?/save" class="fields" oninput={() => (dirty = true)} onchange={() => (dirty = true)} onsubmit={() => (leaving = true)}>
    {#if form?.message}<p class="err" role="alert">{form.message}</p>{/if}
    {#if form?.saved}<p class="ok" role="status">Saved.</p>{/if}

    <fieldset oninput={labelEdited}>
      <legend>Labels</legend>
      <p class="meta">The model's proposal is shown beside each. "As proposed" keeps it; "none" clears it; anything else is your correction.</p>
      {#each Object.entries(data.choices) as [family, values] (family)}
        {@const machine = s.machine.facets[family]}
        {@const human = family in s.human.facets ? s.human.facets[family] : undefined}
        {@const chosen = facetValue(family, human)}
        {@const suggested = !machine && s.suggested?.[family] ? s.suggested[family] : null}
        <label class="row">
          <span class="name">{pretty(family)}</span>
          <select name={`facet:${family}`}>
            <option value="__proposed" selected={chosen === "__proposed"}>as proposed{machine ? `: ${pretty(machine)}` : suggested ? `: (none; a head suggests ${pretty(suggested.value)})` : ": (none)"}</option>
            {#if suggested}<option value={suggested.value} selected={chosen === suggested.value}>accept the suggestion: {pretty(suggested.value)} (from a head, no face found)</option>{/if}
            <option value="__none" selected={chosen === "__none"}>none</option>
            {#each values as val (val)}
              <option value={val} selected={chosen === val}>{pretty(val)}</option>
            {/each}
            {#if !chosen.startsWith("__") && !values.includes(chosen)}
              <!-- a value no stored still carries any more (a refused save after a re-run) -->
              <option value={chosen} selected>{pretty(chosen)}</option>
            {/if}
          </select>
          <input name={`facet:${family}:new`} value={v?.[`facet:${family}:new`] ?? ""} placeholder="or type a new value" aria-label={`new value for ${pretty(family)}`} />
          {#if family === "shot_scale"}
            <!-- The page's reasons that bear on this one label, so a disagreement or a weak guess is visible where it is corrected (#41). -->
            {#each s.reasons.filter((r) => r.k === "split") as r (r.k)}<small class="meta why">This size came from the face; the same person's head reads it as {pretty(r.head)}. Pick whichever is right.</small>{/each}
            {#each s.reasons.filter((r) => r.k === "scale") as r (r.k)}<small class="meta why">Proposed at {r.score}, below the usual cut, from {r.source === "scenery" ? "the scenery tag" : "tags that pointed two ways"}.</small>{/each}
          {/if}
        </label>
      {/each}
      <label class="checked">
        <input type="checkbox" name="labels_checked" checked={labelsChecked} />
        <input type="hidden" name="labels_seen" value={labelsSeen} />
        <span>Labels checked: I looked at every label here. Those left as proposed count as right in the <a href="/admin/labels">label report</a>.{#if checkStale && !v}<br /><small>Checked earlier, before a re-run changed the labels. Tick it again once you have checked these.</small>{/if}</span>
      </label>
    </fieldset>

    <fieldset>
      <legend>Tags</legend>
      <p class="meta">Untick a tag to remove it from this still. Added tags are yours.</p>
      <div class="tags">
        {#each machineTags as t (t)}
          <label class="tag"><input type="checkbox" name="tag" value={t} checked={tagOn(t)} /> {pretty(t)}</label>
        {/each}
        {#each addedTags as t (t)}
          <label class="tag mine"><input type="checkbox" name="tag" value={t} checked={tagOn(t)} /> {pretty(t)} <small>added</small></label>
        {/each}
      </div>
      <label class="row"><span class="name">add tags</span><input name="tags:add" value={v?.["tags:add"] ?? ""} placeholder="comma separated, e.g. rain, umbrella" /></label>
    </fieldset>

    <fieldset>
      <legend>Review</legend>
      <div class="review">
        <label><input type="radio" name="state" value="unreviewed" checked={st === "unreviewed"} /> unreviewed</label>
        <label><input type="radio" name="state" value="kept" checked={st === "kept"} /> keep</label>
        <label><input type="radio" name="state" value="culled" checked={st === "culled"} /> cull</label>
        <label><input type="checkbox" name="locked" checked={v ? v.locked === "on" : s.locked} /> lock (survives re-selection)</label>
        <label><input type="checkbox" name="excluded" checked={v ? v.excluded === "on" : s.excluded} /> hide from the public side</label>
      </div>
      <label class="row"><span class="name">note</span><input name="note" value={v ? v.note ?? "" : s.reviewNote ?? ""} placeholder="why, for later you" /></label>
    </fieldset>

    <div class="actions">
      {#if data.walk.next}<button type="submit" name="next" value={`/admin/stills/${data.walk.next.id}`} class="primary" bind:this={saveNext} title="Ctrl+Enter">Save and next → <kbd aria-hidden="true">Ctrl</kbd><kbd aria-hidden="true">↵</kbd></button>{/if}
      {#if data.walk.nextUnreviewed}<button type="submit" name="next" value={`/admin/stills/${data.walk.nextUnreviewed.id}`}>Save and next unreviewed ↷</button>{/if}
      <button type="submit">Save and stay</button>
      <button type="submit" name="next" value={`/admin/works/${s.workId}#${s.candidate}`}>Save and back to the sheet</button>
    </div>
  </form>
  {/key}
</div>
{@render walk()}

<style>
  .walk {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    gap: var(--s-3);
    min-height: 28px;
    margin: var(--s-2) 0 var(--s-4);
    border-top: 1px solid var(--line-1);
    border-bottom: 1px solid var(--line-1);
    font-size: var(--t-meta);
    color: var(--text-3);
  }
  .walk .pos {
    color: var(--text-2);
    text-align: center;
  }
  .walk .fwd {
    display: flex;
    gap: var(--s-4);
    justify-content: flex-end;
  }
  .walk a {
    white-space: nowrap;
  }
  .editor {
    display: grid;
    gap: var(--s-5);
    grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  }
  @media (max-width: 900px) {
    .editor {
      grid-template-columns: 1fr;
    }
  }
  .pic img {
    width: 100%;
    height: auto;
    display: block;
    outline: 1px solid var(--line-1);
  }
  .fields {
    display: grid;
    gap: var(--s-4);
    align-content: start;
  }
  fieldset {
    border: 1px solid var(--line-1);
    padding: var(--s-2) var(--s-3) var(--s-3);
    display: grid;
    gap: var(--s-2);
    margin: 0;
  }
  legend {
    font: 500 var(--t-label) / 1.4 var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-3);
    padding: 0 var(--s-1);
  }
  .meta {
    margin: 0;
    font-size: var(--t-meta);
  }
  .row .why { flex-basis: 100%; }
  .row {
    display: grid;
    grid-template-columns: 7rem minmax(0, 1fr) minmax(0, 1fr);
    gap: var(--s-2);
    align-items: center;
    color: var(--text-2);
  }
  .name {
    color: var(--text-3);
    text-transform: capitalize;
  }
  select,
  input {
    min-width: 0;
  }
  .checked {
    display: flex;
    gap: var(--s-2);
    align-items: baseline;
    margin-top: var(--s-1);
    color: var(--text-2);
    font-size: var(--t-meta);
  }
  .checked small {
    color: var(--text-3);
  }
  .tags {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1);
  }
  .tag {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    padding: 2px 8px;
    border: 1px solid var(--line-2);
    border-radius: 2px;
    color: var(--text-2);
    font-size: var(--t-meta);
  }
  .tag.mine {
    border-color: var(--accent);
  }
  .tag small {
    color: var(--text-3);
  }
  .review {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2) var(--s-4);
    color: var(--text-2);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2);
  }
  .primary {
    border-color: var(--accent);
    color: var(--text-1);
  }
  .err {
    color: var(--accent);
    margin: 0;
  }
  .ok {
    color: var(--text-2);
    margin: 0;
  }
</style>
