<script>
  import { onMount } from "svelte";
  import { formatTime, mediaUrl, placeholder } from "$lib/format.js";
  import Swatches from "$lib/Swatches.svelte";
  let { data } = $props();

  const pretty = (v) => String(v).replaceAll("_", " ").replaceAll("-", " ");
  const CHIPS_PER_FAMILY = 6;
  const TAGS_SHOWN = 12;

  // Build a URL from the active state with one change applied.
  function url(change = {}) {
    const p = new URLSearchParams();
    const state = { ...data.active, ...change };
    if (state.q) p.set("q", state.q);
    for (const t of state.tags ?? []) p.append("tag", t);
    for (const [k, v] of Object.entries(state)) {
      if (k === "q" || k === "tags" || k === "page" || !v) continue;
      p.set(k, v);
    }
    if (change.page && change.page > 1) p.set("page", String(change.page));
    if (data.browse) p.set("browse", "1"); // "browse everything" stays on across its pages
    const mode = "mode" in change ? change.mode : data.mode;
    if (mode === "mood") p.set("mode", "mood");
    const qs = p.toString();
    return qs ? `/?${qs}` : "/";
  }
  const withTag = (tag) => url({ tags: [...data.active.tags, tag] });
  const withoutTag = (tag) => url({ tags: data.active.tags.filter((t) => t !== tag) });
  const withFacet = (name, value) => url({ [name]: value });
  const withoutFacet = (name) => url({ [name]: null });

  // Chips per family: the active value first, then the top values by count.
  function chips(family) {
    const bucket = data.counts[family] ?? {};
    const active = data.active[family];
    const rest = Object.entries(bucket)
      .filter(([v]) => v !== active)
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, CHIPS_PER_FAMILY);
    return { active, rest };
  }
  const hasConstraint = $derived(Boolean(data.active.q) || data.active.tags.length > 0 || Object.keys(data.facets).some((f) => data.active[f]));

  // The rail is a sidebar from 1000px up and a closed "Narrow" fold below
  // that (research/03: persistent facet rail on desktop, collapsible on
  // tablet). Rendered open on the server so a desktop first paint is right.
  let wide = $state(true);
  const syncWide = () => (wide = window.matchMedia("(min-width: 1000px)").matches);
  onMount(() => {
    syncWide();
    const mq = window.matchMedia("(min-width: 1000px)");
    mq.addEventListener("change", syncWide);
    return () => mq.removeEventListener("change", syncWide);
  });

  // The mode toggle is the page's own state, not only the server's: the
  // highlight and the box's wording follow a click at once (they used to
  // follow the last page load, so picking "by mood" looked like it did
  // nothing). With a query in the box a click re-runs it in the new mode;
  // with an empty box it just changes what the box expects.
  // Writable derived: follows each page load (server render included),
  // and a click overrides it until the next load.
  let mode = $derived(data.mode);
  let searchForm;
  function pickMode() {
    if (searchBox?.value.trim()) searchForm?.requestSubmit();
    else searchBox?.focus();
  }
  const PLACEHOLDER = {
    tags: "Search tags: rain, sword, sitting, sky…",
    mood: "Describe a mood: a lonely figure in a vast empty landscape…",
  };

  // "/" focuses the search box from anywhere on the page.
  let searchBox;
  function onkeydown(e) {
    if (e.key !== "/" || e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = e.target?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    e.preventDefault();
    searchBox?.focus();
  }
</script>

<svelte:window {onkeydown} onresize={syncWide} />

<svelte:head>
  <title>Explore — Hikari Index</title>
</svelte:head>

<h1 class="label">Explore</h1>
<form method="GET" action="/" class="search" role="search" bind:this={searchForm}>
  {#each data.active.tags as t (t)}<input type="hidden" name="tag" value={t} />{/each}
  {#each Object.keys(data.facets) as f (f)}{#if data.active[f]}<input type="hidden" name={f} value={data.active[f]} />{/if}{/each}
  <label>
    <span class="sr">{mode === "mood" ? "Describe a mood" : "Search stills by tag"}</span>
    <input type="search" name="q" value={data.active.q ?? ""} placeholder={PLACEHOLDER[mode]} autocomplete="off" bind:this={searchBox} />
  </label>
  <button type="submit">Search</button>
  <span class="hint mono" aria-hidden="true"><kbd>/</kbd></span>
  <span class="modes" role="radiogroup" aria-label="How to read the search">
    <label class="mode" class:on={mode === "tags"}><input type="radio" name="mode" value="tags" bind:group={mode} onchange={pickMode} /> by tag</label>
    <label class="mode" class:on={mode === "mood"} class:off={!data.encoderConfigured} title={data.encoderConfigured ? "Rank every still by how well it matches a description" : "No text encoder is configured"}><input type="radio" name="mode" value="mood" bind:group={mode} onchange={pickMode} disabled={!data.encoderConfigured} /> by mood</label>
  </span>
</form>

<div class="active" aria-label="Active filters">
  {#if data.interpreted}<span class="note">“{data.interpreted.query}” read as {pretty(data.interpreted.family)}: {pretty(data.interpreted.value)}</span>{/if}
  {#if data.active.q}<a class="chip on" href={url({ q: "" })}>“{data.active.q}” ×</a>{/if}
  {#each data.active.tags as t (t)}<a class="chip on" href={withoutTag(t)}>{pretty(t)} ×</a>{/each}
  {#each Object.keys(data.facets) as f (f)}
    {#if data.active[f]}<a class="chip on" href={withoutFacet(f)}>{pretty(f)}: {pretty(data.active[f])} ×</a>{/if}
  {/each}
  {#if hasConstraint}<a class="reset" href="/">clear all</a>{/if}
</div>

<div class="explore" class:wide>
  <details class="rail" open={wide} aria-label="Narrow these results">
    <summary class="label">Narrow</summary>
    {#if data.mode === "mood" && data.active.q && !data.moodUnavailable}
      <p class="meta railnote">Counts are before the mood ranking: a chip narrows the stills, then the closest {data.moodPool} are shown.</p>
    {/if}
    <div class="families">
      {#if data.tags.length}
        <div class="family">
          <span class="label">tags</span>
          <div class="row">
            {#each data.tags.slice(0, TAGS_SHOWN) as t (t.tag)}
              <a class="chip" href={withTag(t.tag)}>{pretty(t.tag)} <b>{t.count}</b></a>
            {/each}
          </div>
          {#if data.tags.length > TAGS_SHOWN}
            <details class="more">
              <summary>all {data.tags.length}</summary>
              <div class="row">
                {#each data.tags.slice(TAGS_SHOWN) as t (t.tag)}
                  <a class="chip" href={withTag(t.tag)}>{pretty(t.tag)} <b>{t.count}</b></a>
                {/each}
              </div>
            </details>
          {/if}
        </div>
      {/if}
      {#each Object.keys(data.facets) as family (family)}
        {@const c = chips(family)}
        {#if c.active || c.rest.length}
          <div class="family">
            <span class="label">{pretty(family)}</span>
            <div class="row">
              {#if c.active}<a class="chip on" href={withoutFacet(family)}>{pretty(c.active)} ×</a>{/if}
              {#each c.rest as [value, n] (value)}
                <a class="chip" href={withFacet(family, value)}>{pretty(value)} <b>{n}</b></a>
              {/each}
            </div>
          </div>
        {/if}
      {/each}
    </div>
  </details>

  <div class="results">
    {#if data.frontDoor}
      <section class="door">
        <p class="lead">Stills from anime, chosen for how they look: a reference library of framing, light and color. Search by tag or describe a mood, or start from one of these.</p>
        <ul class="doors">
          <li><a href="/series"><strong>Library</strong><span>by franchise, season and episode, in order</span></a></li>
          <li><a href="/palette"><strong>Palette</strong><span>by how a frame is graded: its shadows and its highlights</span></a></li>
          <li><a href="/composition"><strong>Techniques</strong><span>by shot scale, angle, lighting and framing, by name</span></a></li>
        </ul>
      </section>
      <p class="meta count">A few from across the library today · <span class="mono">{data.total}</span> stills in all</p>
    {:else if data.mode === "mood" && data.active.q && data.moodUnavailable}
      <p class="meta count" role="status">
        Mood search is not available right now{data.encoderConfigured ? ": the text encoder did not answer" : ": no text encoder is configured"}.
        <a href={url({ mode: "tags" })}>Search “{data.active.q}” by tag instead</a>.
      </p>
    {:else if data.mode === "mood" && data.active.q}
      <p class="meta count" role="status">
        The <span class="mono">{data.total}</span> closest stills to “{data.active.q}”, by how the picture reads rather than its tags{data.pages > 1 ? ` · page ${data.page} of ${data.pages}` : ""}.
        <a href={url({ mode: "tags" })}>By tag instead</a>.
      </p>
    {:else if data.total === 0}
      <p class="meta count" role="status">
        No stills match.
        {#if data.withoutQuery != null}Without “{data.active.q}” there would be <a href={url({ q: "" })}>{data.withoutQuery}</a>.{/if}
        {#if data.active.q && data.encoderConfigured}<a href={url({ mode: "mood" })}>Try “{data.active.q}” as a mood instead</a>.{/if}
      </p>
    {:else}
      <p class="meta count mono" role="status">{data.total} stills{data.pages > 1 ? ` · page ${data.page} of ${data.pages}` : ""}</p>
    {/if}

    <ul class="cards">
      {#each data.frontDoor ? data.sample : data.stills as still, index (still.id)}
        <li>
          <a href={`/still/${still.id}`}>
            <span class="frame">
              <img
                style:background-color={placeholder(still)}
                srcset={still.tiers.map((t) => `${mediaUrl(t)} ${t.w}w`).join(", ")}
                sizes="(max-width: 600px) 50vw, (max-width: 1200px) 33vw, 14vw"
                src={mediaUrl(still.tiers[0])}
                width={still.tiers.at(-1).w}
                height={still.tiers.at(-1).h}
                loading={index < 8 ? "eager" : "lazy"}
                alt={`${still.workLabel ?? still.workId}, still at ${formatTime(still.ts)}${still.facets.shot_scale ? `, ${pretty(still.facets.shot_scale)} shot` : ""}`}
              />
            </span>
            <Swatches palette={still.palette} height="6px" />
            <span class="cap">{still.workLabel ?? still.workId}</span>
            <span class="sub mono">{formatTime(still.ts) || still.candidate}</span>
          </a>
        </li>
      {/each}
    </ul>

    {#if data.frontDoor}
      <nav class="pages" aria-label="Everything">
        <a href="/?browse=1">Browse all {data.total} stills, most recently added first →</a>
      </nav>
    {:else if data.pages > 1}
      <nav class="pages" aria-label="Pagination">
        {#if data.page > 1}<a href={url({ page: data.page - 1 })}>← Previous</a>{/if}
        {#if data.page < data.pages}<a href={url({ page: data.page + 1 })}>Next →</a>{/if}
      </nav>
    {/if}
  </div>
</div>

<style>
  h1.label {
    margin: var(--s-2) 0 var(--s-3);
  }
  .search {
    display: flex;
    gap: var(--s-2);
    align-items: center;
    margin: 0 0 var(--s-3);
  }
  .search label {
    flex: 1;
  }
  .search input {
    width: 100%;
    height: 44px;
    font-size: 17px;
  }
  .search button {
    height: 44px;
  }
  .hint {
    color: var(--text-3);
  }
  .modes {
    display: inline-flex;
    gap: var(--s-1);
    margin-left: var(--s-2);
  }
  .mode {
    display: inline-flex;
    align-items: center;
    height: 44px;
    padding: 0 var(--s-3);
    border: 1px solid var(--line-2);
    border-radius: 2px;
    color: var(--text-2);
    font-size: var(--t-ui);
    white-space: nowrap;
    cursor: pointer;
  }
  .mode input {
    position: absolute;
    opacity: 0;
    width: 1px;
    height: 1px;
  }
  .mode:has(input:focus-visible) {
    outline: 2px solid var(--focus);
    outline-offset: 3px;
  }
  .mode.off {
    color: var(--text-3);
    cursor: not-allowed;
  }
  .mode.on {
    border-color: var(--accent);
    color: var(--text-1);
  }
  .active,
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1) var(--s-2);
    align-items: center;
  }
  .active {
    min-height: 1.6rem;
    margin-bottom: var(--s-3);
  }
  .note,
  .reset {
    font-size: var(--t-meta);
    color: var(--text-3);
  }
  .reset {
    color: var(--text-2);
    margin-left: var(--s-2);
  }

  /* the rail and the results */
  .explore {
    display: grid;
    gap: var(--s-4);
    grid-template-columns: minmax(0, 1fr);
    align-items: start;
  }
  .explore.wide {
    grid-template-columns: 200px minmax(0, 1fr);
    gap: var(--s-5);
  }
  .rail {
    border-top: 1px solid var(--line-1);
    padding-top: var(--s-3);
  }
  .rail > summary {
    cursor: pointer;
    list-style: none;
    margin: 0;
  }
  .rail > summary::-webkit-details-marker {
    display: none;
  }
  .rail > summary::after {
    content: " ▸";
  }
  .rail[open] > summary::after {
    content: " ▾";
  }
  .explore.wide .rail {
    position: sticky;
    top: var(--s-4);
    max-height: calc(100vh - var(--s-6));
    overflow-y: auto;
    border-top: 0;
    padding-top: 0;
  }
  .explore.wide .rail > summary {
    pointer-events: none; /* always open as a sidebar */
  }
  .explore.wide .rail > summary::after {
    content: "";
  }
  .railnote {
    margin: var(--s-2) 0 0;
  }
  .families {
    display: grid;
    gap: var(--s-4);
    margin-top: var(--s-3);
  }
  .family .label {
    margin-bottom: var(--s-2);
  }
  .more {
    margin-top: var(--s-2);
  }
  .more summary {
    cursor: pointer;
    font-size: var(--t-meta);
    color: var(--text-2);
  }
  .more .row {
    margin-top: var(--s-2);
  }

  .door {
    margin: 0 0 var(--s-5);
  }
  .doors {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    gap: var(--s-3);
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
  .doors a {
    display: block;
    padding: var(--s-3) var(--s-4);
    background: var(--bg-1);
    border: 1px solid var(--line-1);
    text-decoration: none;
    color: var(--text-1);
    transition: border-color 120ms;
  }
  .doors a:hover {
    border-color: var(--line-2);
  }
  .doors strong {
    display: block;
    font-size: var(--t-ui);
    font-weight: 500;
  }
  .doors span {
    color: var(--text-3);
    font-size: var(--t-meta);
  }
  .count {
    margin: 0 0 var(--s-3);
  }
  .cards {
    --card-min: 210px; /* 7 across beside the rail at 1920, 5 at 1400 */
    gap: var(--s-3) var(--s-4);
  }
  @media (max-width: 600px) {
    .hint {
      display: none;
    }
  }
</style>
