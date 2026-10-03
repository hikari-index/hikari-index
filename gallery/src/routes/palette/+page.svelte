<script>
  import { formatTime, mediaUrl, placeholder, sentence } from "$lib/format.js";
  import { textOn, needsKeyline } from "$lib/color.js";
  import GradeStrip from "$lib/GradeStrip.svelte";
  let { data } = $props();

  const bare = (hex) => hex.replace("#", "");
  const pretty = (v) => String(v ?? "").replaceAll("-", " ");

  function pickUrl(hexes, match) {
    const u = new URLSearchParams();
    for (const h of hexes) u.append("c", bare(h));
    if (match === "broad") u.set("match", "broad");
    return `/palette?${u}`;
  }
  function gradeUrl({ shadow, highlight, shadows, highlights, page } = {}) {
    const u = new URLSearchParams();
    if (shadow) u.set("shadow", bare(shadow));
    else if (shadows) u.set("shadows", shadows);
    if (highlight) u.set("highlight", bare(highlight));
    else if (highlights) u.set("highlights", highlights);
    if (page && page > 1) u.set("page", String(page));
    const qs = u.toString();
    return qs ? `/palette?${qs}` : "/palette";
  }
  // Current grade selection, so a row click keeps the other half.
  const sel = $derived({ shadow: data.shadow, highlight: data.highlight, shadows: data.shadowTone, highlights: data.highlightTone });
  const title = $derived(
    data.mode === "near"
      ? `Similar by palette to ${data.target.workLabel} at ${formatTime(data.target.ts)}`
      : data.mode === "grade"
        ? [data.shadowName && `${pretty(data.shadowName)} shadows`, data.highlightName && `${pretty(data.highlightName)} highlights`].filter(Boolean).join(", ")
        : data.mode === "pick"
          ? `Stills with ${data.chosen.join(", ")}`
          : "Palette"
  );
  function pageUrl(page) {
    if (data.mode === "pick") return `${pickUrl(data.chosen, data.match)}${page > 1 ? `&page=${page}` : ""}`;
    return gradeUrl({ ...sel, page });
  }
</script>

<svelte:head>
  <title>{title} — Hikari Index</title>
</svelte:head>

{#snippet swatchRow(chips, lane, current)}
  <div class="row" role="group" aria-label={lane}>
    {#each chips as chip (chip.hex)}
      <a
        class="sw mono"
        class:on={current === chip.hex}
        class:keyed={needsKeyline(chip.hex)}
        href={gradeUrl({ ...sel, [lane === "shadows" ? "shadow" : "highlight"]: chip.hex, [lane]: null })}
        style:background={chip.hex}
        style:color={textOn(chip.hex)}
        title={`${chip.hex} · ${chip.count} stills`}
        aria-label={`${pretty(chip.tone)} ${lane === "shadows" ? "shadow" : "highlight"} ${chip.hex}, ${chip.count} stills`}
      ><span>{chip.count}</span></a>
    {/each}
  </div>
{/snippet}

{#snippet grid(results)}
  <ul class="cards">
    {#each results as still (still.id)}
      <li>
        <a href={`/still/${still.id}`}>
          <span class="frame">
            <img style:background-color={placeholder(still)} src={mediaUrl(still.tiers[Math.min(1, still.tiers.length - 1)])} width={still.tiers.at(-1).w} height={still.tiers.at(-1).h} loading="lazy" alt={`${still.workLabel ?? still.workId}, still at ${formatTime(still.ts)}`} />
          </span>
          <GradeStrip palette={still.palette} height="6px" />
          <span class="cap">{still.workLabel ?? still.workId}</span>
          <span class="sub mono">{formatTime(still.ts) || still.candidate}</span>
        </a>
      </li>
    {/each}
  </ul>
{/snippet}

{#snippet pager()}
  {#if data.pages > 1}
    <nav class="pages" aria-label="Pagination">
      {#if data.page > 1}<a href={pageUrl(data.page - 1)}>← Previous</a>{/if}
      <span class="mono">page {data.page} of {data.pages}</span>
      {#if data.page < data.pages}<a href={pageUrl(data.page + 1)}>Next →</a>{/if}
    </nav>
  {/if}
{/snippet}

{#if data.mode === "grades"}
  <h1>Palette</h1>
  <p class="meta">Browse by how a frame is graded: what color its shadows go, and what color its highlights go. Pick a common grade, or choose a shadow and a highlight yourself.</p>

  <h2>Common grades</h2>
  <ul class="tiles">
    {#each data.common as g (g.label)}
      <li>
        <a href={gradeUrl({ shadows: g.shadowTone, highlights: g.highlightTone })} aria-label={`${g.label}, ${g.count} stills`}>
          <span class="mosaic">
            {#each g.covers as s (s.id)}<img style:background-color={placeholder(s)} src={mediaUrl(s.tiers[0])} width={s.tiers[0].w} height={s.tiers[0].h} loading="lazy" alt="" />{/each}
          </span>
          <strong><span class="pair"><i style:background={g.shadowHex} class:keyed={needsKeyline(g.shadowHex)}></i><i style:background={g.highlightHex} class:keyed={needsKeyline(g.highlightHex)}></i></span>{sentence(g.label)} <span class="count">{g.count}</span></strong>
        </a>
      </li>
    {/each}
  </ul>

  <h2>Shadows</h2>
  <p class="meta">The colors the library's darks go. Pick one.</p>
  {@render swatchRow(data.shadows, "shadows", null)}
  <h2>Highlights</h2>
  <p class="meta">The colors its lights go. Pick one, on its own or with a shadow.</p>
  {@render swatchRow(data.highlights, "highlights", null)}

  <p class="meta more"><a href="/palette?view=colors">Browse by a single color instead</a></p>

{:else if data.mode === "grade"}
  <nav class="crumbs"><a href="/palette">← Palette</a></nav>
  <h1>
    <span class="pair big">
      {#each [data.shadow ?? data.shadowToneHex, data.highlight ?? data.highlightToneHex] as hex, i (i)}
        <i style:background={hex ?? "transparent"} class:tone={!hex} class:keyed={hex && needsKeyline(hex)}></i>
      {/each}
    </span>
    {title}
  </h1>
  <div class="refine">
    <span class="label">Shadows{#if data.shadow || data.shadowTone}{" · "}<a href={gradeUrl({ ...sel, shadow: null, shadows: null })}>any</a>{/if}</span>
    {@render swatchRow(data.shadows, "shadows", data.shadow)}
    <span class="label">Highlights{#if data.highlight || data.highlightTone}{" · "}<a href={gradeUrl({ ...sel, highlight: null, highlights: null })}>any</a>{/if}</span>
    {@render swatchRow(data.highlights, "highlights", data.highlight)}
  </div>
  <p class="meta mono" role="status">{data.total} of {data.all} stills{data.pages > 1 ? ` · page ${data.page} of ${data.pages}` : ""}</p>
  {#if data.more?.length}
    <p class="meta">Add a color to narrow these:</p>
    <ul class="cloud small" aria-label="Add a color">
      {#each data.more as chip (chip.hex)}
        <li><a href={pickUrl([...data.chosen, chip.hex], data.match)} class:keyed={needsKeyline(chip.hex)} style:background={chip.hex} style:color={textOn(chip.hex)} title={chip.hex} aria-label={`add ${chip.name}, ${chip.count} of these stills`}>{chip.name}<span class="mono">{chip.count}</span></a></li>
      {/each}
    </ul>
  {/if}
  {@render grid(data.results)}
  {@render pager()}

{:else if data.mode === "cloud"}
  <nav class="crumbs"><a href="/palette">← Palette</a></nav>
  <h1>Browse by color</h1>
  <p class="meta">The sixty colors the library's stills are most made of, with how many stills carry each. Pick one to see the stills that carry it.</p>
  <p class="filters" role="group" aria-label="Order">
    order:
    <a href="/palette?view=colors" class:on={data.order === "color"}>by color</a>
    <a href="/palette?view=colors&order=count" class:on={data.order === "count"}>by count</a>
  </p>
  <ul class="cloud" aria-label="Color swatches">
    {#each data.chips as chip (chip.hex)}
      <li>
        <a href={pickUrl([chip.hex], "strict")} class:keyed={needsKeyline(chip.hex)} style:background={chip.hex} style:color={textOn(chip.hex)} title={chip.hex} aria-label={`${chip.name}, ${chip.count} stills`}>{chip.name}<span class="mono">{chip.count}</span></a>
      </li>
    {/each}
  </ul>

{:else if data.mode === "pick"}
  <nav class="crumbs"><a href="/palette?view=colors">← Browse by color</a></nav>
  <h1>
    Stills with
    {#each data.chosen as hex (hex)}
      {@const rest = data.chosen.filter((h) => h !== hex)}
      <a class="chosen mono" class:keyed={needsKeyline(hex)} href={rest.length ? pickUrl(rest, data.match) : "/palette?view=colors"} style:background={hex} style:color={textOn(hex)} title="remove this color">{hex} ×</a>
    {/each}
  </h1>
  <p class="filters" role="group" aria-label="How close a color has to be">
    match:
    <a href={pickUrl(data.chosen, "strict")} class:on={data.match === "strict"}>strict</a>
    <a href={pickUrl(data.chosen, "broad")} class:on={data.match === "broad"}>broad</a>
  </p>
  <p class="meta mono" role="status">{data.total} of {data.all} stills{data.pages > 1 ? ` · page ${data.page} of ${data.pages}` : ""}</p>
  {#if data.more?.length}
    <p class="meta">Add a color to narrow these:</p>
    <ul class="cloud small" aria-label="Add a color">
      {#each data.more as chip (chip.hex)}
        <li><a href={pickUrl([...data.chosen, chip.hex], data.match)} class:keyed={needsKeyline(chip.hex)} style:background={chip.hex} style:color={textOn(chip.hex)} title={chip.hex} aria-label={`add ${chip.name}, ${chip.count} of these stills`}>{chip.name}<span class="mono">{chip.count}</span></a></li>
      {/each}
    </ul>
  {/if}
  {@render grid(data.results)}
  {@render pager()}

{:else}
  <nav class="crumbs">
    <a href={`/still/${data.target.id}`}>← Back to the still</a>
    <a href={`/similar/${data.target.id}`}>Similar by image instead</a>
  </nav>
  <h1>Similar by palette</h1>
  <div class="target">
    <span class="frame">
      <img style:background-color={placeholder(data.target)} src={mediaUrl(data.target.tiers[Math.min(1, data.target.tiers.length - 1)])} width={data.target.tiers.at(-1).w} height={data.target.tiers.at(-1).h} alt={`${data.target.workLabel}, still at ${formatTime(data.target.ts)}, the query still`} />
    </span>
    <GradeStrip palette={data.target.palette} height="8px" />
    <p class="meta">{data.target.workLabel} · <span class="mono">{formatTime(data.target.ts) || data.target.candidate}</span></p>
  </div>
  {@const nearBase = `/palette?near=${encodeURIComponent(data.target.id)}`}
  <p class="filters" role="group" aria-label="Search within">
    within:
    <a href={nearBase} class:on={data.scope === "all"}>everything</a>
    <a href={`${nearBase}&scope=franchise`} class:on={data.scope === "franchise"}>this franchise</a>
    <a href={`${nearBase}&scope=season`} class:on={data.scope === "season"}>this season</a>
    <a href={`${nearBase}&scope=work`} class:on={data.scope === "work"}>this episode</a>
  </p>
  {#if data.target.palette}
    <p class="meta" role="status">{data.results.length} nearest by palette: the closest overall color balance, brightness and saturation, whatever is in the picture.</p>
    {@render grid(data.results)}
  {:else}
    <p class="meta" role="status">This still has no palette (no usable colors were found in the frame), so there is nothing to compare by color. <a href={`/similar/${data.target.id}`}>Similar by image</a> still works.</p>
  {/if}
{/if}

<style>
  h1 {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2) var(--s-3);
    align-items: center;
  }
  .more {
    margin-top: var(--s-5);
  }
  .pair {
    display: inline-flex;
    gap: 2px;
    vertical-align: middle;
    margin-right: var(--s-2);
  }
  .pair i {
    display: inline-block;
    width: 12px;
    height: 12px;
  }
  .pair.big i {
    width: 20px;
    height: 20px;
  }
  .pair i.tone {
    outline: 1px dashed var(--line-2);
    outline-offset: -1px;
  }
  .keyed {
    outline: 1px solid var(--line-2);
    outline-offset: -1px;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1);
    margin: var(--s-2) 0 var(--s-4);
  }
  .sw {
    display: inline-flex;
    align-items: flex-end;
    justify-content: flex-end;
    width: 44px;
    height: 32px;
    text-decoration: none;
    font-size: var(--t-label);
    padding: 0 4px 2px 0;
    box-sizing: border-box;
  }
  .sw.on {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .refine {
    margin: var(--s-3) 0 var(--s-2);
  }
  .refine .label {
    margin-top: var(--s-3);
  }
  .cloud {
    list-style: none;
    margin: var(--s-3) 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1);
  }
  .cloud a {
    display: inline-flex;
    gap: var(--s-2);
    align-items: baseline;
    padding: 5px 10px;
    text-decoration: none;
    font-size: var(--t-meta);
    text-transform: capitalize;
  }
  .cloud a span {
    font-size: var(--t-label);
  }
  .cloud.small a {
    font-size: var(--t-meta);
    padding: 2px 8px;
  }
  .chosen {
    font-size: var(--t-ui);
    font-weight: 400;
    padding: 3px 8px;
    text-decoration: none;
  }
  .cards {
    gap: var(--s-3) var(--s-4);
  }
  .target {
    max-width: 22rem;
    margin: var(--s-3) 0 var(--s-2);
  }
  .target .frame {
    outline-color: var(--line-1);
  }
</style>
