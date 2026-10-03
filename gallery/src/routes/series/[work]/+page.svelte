<script>
  import { formatTime, mediaUrl, placeholder } from "$lib/format.js";
  import Swatches from "$lib/Swatches.svelte";
  let { data } = $props();

  const KEY_FACETS = ["shot_scale", "setting", "time", "lighting"];

  function srcset(still) {
    return still.tiers.map((t) => `${mediaUrl(t)} ${t.w}w`).join(", ");
  }
  // The framing facets when any is there; otherwise whatever else was
  // labeled (a still is rarely unlabeled, just not in these four).
  // Label values read as words here as everywhere else ("close up").
  const pretty = (v) => String(v).replaceAll("-", " ").replaceAll("_", " ");
  function chips(still) {
    const key = KEY_FACETS.filter((f) => still.facets[f]).map((f) => still.facets[f]);
    return (key.length ? key : Object.values(still.facets).filter(Boolean).slice(0, 3)).map(pretty);
  }
  // Every still in an episode shares one aspect ratio, so the sheet's image
  // slots take the work's own ratio and nothing is letterboxed.
  const ratio = $derived.by(() => {
    const t = data.stills[0]?.tiers.at(-1);
    return t && t.w && t.h ? `${t.w} / ${t.h}` : "16 / 9";
  });
  const code = $derived([data.work.episode, data.work.episodeTitle].filter(Boolean).join(" · "));
</script>

<svelte:head>
  <title>{data.work.label} — Hikari Index</title>
</svelte:head>

<nav class="crumbs">
  <a href="/series">← Library</a>
  {#if data.around?.franchise}<a href={`/franchise/${data.around.franchise.id}`}>{data.around.franchise.name}</a>{/if}
  {#if data.around?.season && data.around.season.name !== data.around.franchise?.name}<span class="meta">{data.around.season.name}</span>{/if}
</nav>
<header class="sheet">
  {#if code}<span class="label">{code}</span>{/if}
  <h1>{data.work.title}</h1>
  <p class="meta mono">
    {data.stills.length} stills{#if data.span != null}{" · "}{formatTime(data.span)} from first to last{/if}{" · "}in time order
  </p>
</header>

{#snippet walk()}
  {#if data.around?.prev || data.around?.next}
    <nav class="walk mono" aria-label="Neighbouring episodes">
      <span>{#if data.around.prev}<a href={`/series/${data.around.prev.id}`}>← {data.around.prev.episode ?? data.around.prev.title}{data.around.prev.episodeTitle ? ` · ${data.around.prev.episodeTitle}` : ""}</a>{/if}</span>
      <span class="fwd">{#if data.around.next}<a href={`/series/${data.around.next.id}`}>{data.around.next.episode ?? data.around.next.title}{data.around.next.episodeTitle ? ` · ${data.around.next.episodeTitle}` : ""} →</a>{/if}</span>
    </nav>
  {/if}
{/snippet}
{@render walk()}

<ol class="cards" style:--frame-ratio={ratio}>
  {#each data.stills as still, index (still.id)}
    <li id={still.candidate}>
      <a href={`/still/${still.id}`}>
        <span class="frame">
          <img style:background-color={placeholder(still)}
            srcset={srcset(still)}
            sizes="(max-width: 600px) 50vw, (max-width: 1200px) 25vw, 14vw"
            src={mediaUrl(still.tiers[0])}
            width={still.tiers.at(-1).w}
            height={still.tiers.at(-1).h}
            loading={index < 14 ? "eager" : "lazy"}
            alt={`${data.work.label}, still at ${formatTime(still.ts)}${still.facets.shot_scale ? `, ${pretty(still.facets.shot_scale)} shot` : ""}`}
          />
        </span>
        <Swatches palette={still.palette} height="6px" />
        <span class="cap mono"><span class="fig">{index + 1}</span><span class="time">{formatTime(still.ts)}</span></span>
        {#if chips(still).length}
          <span class="sub">{chips(still).join(" · ")}</span>
        {:else}
          <span class="sub unlabeled">no labels</span>
        {/if}
      </a>
    </li>
  {/each}
</ol>
{@render walk()}

<style>
  .crumbs {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-3);
  }
  .walk {
    display: flex;
    justify-content: space-between;
    gap: var(--s-3);
    margin: var(--s-2) 0;
    font-size: var(--t-meta);
  }
  .walk .fwd {
    margin-left: auto;
  }
  .sheet {
    padding: var(--s-4) 0 var(--s-5);
  }
  .sheet h1 {
    margin-top: var(--s-1);
  }
  .cards {
    --card-min: 176px;
  }
</style>
