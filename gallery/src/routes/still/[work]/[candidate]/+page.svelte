<script>
  import { goto } from "$app/navigation";
  import { formatTime, mediaUrl, placeholder } from "$lib/format.js";
  import { needsKeyline } from "$lib/color.js";
  import Swatches from "$lib/Swatches.svelte";
  let { data } = $props();
  const still = $derived(data.still);
  const detail = $derived(still.tiers[still.tiers.length - 1]);
  const pretty = (v) => String(v).replaceAll("-", " ").replaceAll("_", " ");
  // An episode's code, or the part's name for one film of several (the
  // heading is the series title, which the parts share).
  const code = $derived([data.work.episode ?? data.work.episodeTitle, formatTime(still.ts)].filter(Boolean).join(" · "));

  // Arrow keys walk the episode; ignored while typing in a field.
  function onkeydown(e) {
    if (e.altKey || e.ctrlKey || e.metaKey) return;
    const tag = e.target?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    if (e.key === "ArrowLeft" && data.prev) goto(`/still/${data.prev.id}`);
    else if (e.key === "ArrowRight" && data.next) goto(`/still/${data.next.id}`);
  }
</script>

<svelte:window {onkeydown} />

<svelte:head>
  <title>{data.work.label} at {formatTime(still.ts)} — Hikari Index</title>
</svelte:head>

<nav class="crumbs">
  <a href={`/series/${still.workId}#${still.candidate}`}>← {data.work.label}</a>
  <a href="/">Explore</a>
</nav>

<article>
  <div class="hero">
    <img style:background-color={placeholder(still)}
      src={mediaUrl(detail)}
      width={detail.w}
      height={detail.h}
      alt={`${data.work.label}, still at ${formatTime(still.ts)}, detail view`}
    />
  </div>
  <nav class="databar mono" aria-label="Adjacent stills">
    <span>{#if data.prev}<a href={`/still/${data.prev.id}`}>← {formatTime(data.prev.ts)}</a>{/if}</span>
    <span class="pos">{#if data.position}{data.position} of {data.total}{:else}not on the sheet{/if}{#if detail.w}{" · "}{detail.w}×{detail.h}{/if}</span>
    <span class="next">{#if data.next}<kbd aria-hidden="true">←</kbd><kbd aria-hidden="true">→</kbd> <a href={`/still/${data.next.id}`}>{formatTime(data.next.ts)} →</a>{/if}</span>
  </nav>

  <div class="card">
    <span class="label">{code}</span>
    <h1>{data.work.title ?? data.work.label}</h1>
    <p class="actions" aria-label="Actions on this still">
      <a href={`/similar/${still.id}`}>Similar by image</a>
      {#if still.palette}<a href={`/palette?near=${encodeURIComponent(still.id)}`}>Similar by palette</a>{/if}
    </p>
    <div class="facts">
    {#if still.palette}
      <section class="palette" aria-label="Palette">
        <Swatches palette={still.palette} height="20px" />
        <div class="lanes">
          {#each [["shadows", still.palette.shadows], ["midtones", still.palette.midtones], ["highlights", still.palette.highlights]] as [name, sw]}
            <div class="lane">
              <span class="label">{name}</span>
              <span class="row">
                {#each sw as s (s.hex)}
                  <span class="sw">
                    <a class="chip-c" class:keyed={needsKeyline(s.hex)} href={`/palette?c=${s.hex.replace("#", "")}`} style:background={s.hex} title={`stills with ${s.hex}`} aria-label={`stills with ${s.hex}`}></a>
                    <code>{s.hex}</code>
                  </span>
                {/each}
              </span>
            </div>
          {/each}
        </div>
        <p class="meta mono">
          brightness {Math.round(still.palette.luma.p50 * 100)}% · saturation {Math.round(still.palette.sat * 100)}%
        </p>
      </section>
    {/if}
    <dl>
      <dt>Timestamp</dt>
      <dd>{formatTime(still.ts) || "unknown"}</dd>
      {#each Object.entries(still.facets) as [name, value]}
        <dt>{name === "time" ? "time of day" : name.replace("_", " ")}</dt>
        <dd><a href={`/?${name}=${encodeURIComponent(value)}`}>{pretty(value)}</a></dd>
      {/each}
      {#if !Object.keys(still.facets).length}
        <dt>Labels</dt>
        <dd>none</dd>
      {/if}
      {#if still.tags.length}
        <dt>Tags</dt>
        <dd>
          {#each still.tags as tag, i}{#if i > 0}{", "}{/if}<a href={`/?tag=${encodeURIComponent(tag)}`}>{pretty(tag)}</a>{/each}
        </dd>
      {/if}
    </dl>
    </div>
  </div>
</article>

<style>
  .hero {
    display: flex;
    justify-content: center;
    padding-top: var(--s-2);
  }
  .hero img {
    max-height: calc(100vh - 9rem);
    max-width: 100%;
    width: auto;
    height: auto;
    outline: 1px solid var(--line-1);
  }
  .databar {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    min-height: 28px;
    margin-top: var(--s-3);
    border-top: 1px solid var(--line-1);
    border-bottom: 1px solid var(--line-1);
    font-size: var(--t-meta);
    color: var(--text-3);
  }
  .pos {
    text-align: center;
    color: var(--text-2);
  }
  .next {
    text-align: right;
  }
  .card {
    max-width: 640px;
    margin-top: var(--s-6);
  }
  /* wide screens: the palette and the facts sit side by side under the title */
  @media (min-width: 1100px) {
    .card {
      max-width: 1100px;
    }
    .facts {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 0 var(--s-7);
      align-items: start;
    }
    .palette {
      margin-bottom: 0;
    }
  }
  .card h1 {
    margin-top: var(--s-1);
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-5);
    margin: var(--s-4) 0 var(--s-5);
    font-size: var(--t-ui);
  }
  .palette {
    margin: 0 0 var(--s-5);
  }
  .lanes {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-6);
    margin-top: var(--s-4);
  }
  .lane {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .row {
    display: flex;
    gap: var(--s-2);
  }
  .sw {
    display: flex;
    flex-direction: column;
    gap: var(--s-1);
  }
  .chip-c {
    display: block;
    width: 56px;
    height: 40px;
  }
  .chip-c.keyed {
    outline: 1px solid var(--line-2);
    outline-offset: -1px;
  }
  .chip-c:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 3px;
  }
  .sw code {
    font-size: var(--t-meta);
    color: var(--text-3);
  }
  dl {
    display: grid;
    grid-template-columns: 9rem 1fr;
    gap: 6px var(--s-4);
    font-size: var(--t-ui);
    margin: 0;
  }
  dt {
    font: 500 var(--t-label) / 1.4 var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-3);
    align-self: baseline;
  }
  dd {
    margin: 0;
    color: var(--text-2);
  }
  @media (max-width: 600px) {
    .hero img {
      max-height: none;
      width: 100%;
    }
    dl {
      grid-template-columns: 1fr;
      gap: 2px;
    }
    dt {
      margin-top: var(--s-2);
    }
  }
</style>
