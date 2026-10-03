<script>
  import { formatTime, mediaUrl, placeholder } from "$lib/format.js";
  import Swatches from "$lib/Swatches.svelte";
  let { data } = $props();
  const target = $derived(data.target);
</script>

<svelte:head>
  <title>Similar by image to {data.work?.label ?? target.workId} at {formatTime(target.ts)} — Hikari Index</title>
</svelte:head>

<nav class="crumbs">
  <a href={`/still/${target.id}`}>← Back to the still</a>
  <a href={`/palette?near=${encodeURIComponent(target.id)}`}>Similar by palette instead</a>
</nav>

<h1>Similar by image</h1>
<div class="target">
  <span class="frame">
    <img style:background-color={placeholder(target)} src={mediaUrl(target.tiers[Math.min(1, target.tiers.length - 1)])} width={target.tiers.at(-1).w} height={target.tiers.at(-1).h} alt={`${data.work?.label ?? target.workId}, still at ${formatTime(target.ts)}, the query still`} />
  </span>
  <Swatches palette={target.palette} height="8px" />
  <p class="meta">{data.work?.label ?? target.workId} · <span class="mono">{formatTime(target.ts) || target.candidate}</span></p>
</div>

<p class="filters" role="group" aria-label="Search within">
  within:
  <a href={`/similar/${target.id}`} class:on={data.scope === "all"}>everything</a>
  <a href={`/similar/${target.id}?scope=franchise`} class:on={data.scope === "franchise"}>this franchise</a>
  <a href={`/similar/${target.id}?scope=season`} class:on={data.scope === "season"}>this season</a>
  <a href={`/similar/${target.id}?scope=work`} class:on={data.scope === "work"}>this episode</a>
</p>

{#if data.results.length}
  <p class="meta" role="status">{data.results.length} nearest by image embedding: what is in the picture and its style, not its colors or framing. For colors use <a href={`/palette?near=${encodeURIComponent(target.id)}`}>similar by palette</a>.</p>
  <ul class="cards">
    {#each data.results as still (still.id)}
      <li>
        <a href={`/still/${still.id}`}>
          <span class="frame">
            <img style:background-color={placeholder(still)} src={mediaUrl(still.tiers[Math.min(1, still.tiers.length - 1)])} width={still.tiers.at(-1).w} height={still.tiers.at(-1).h} loading="lazy" alt={`${still.work?.label ?? still.workId}, still at ${formatTime(still.ts)}`} />
          </span>
          <Swatches palette={still.palette} height="6px" />
          <span class="cap">{still.work?.label ?? still.workId}</span>
          <span class="sub mono">{formatTime(still.ts) || still.candidate}</span>
        </a>
      </li>
    {/each}
  </ul>
{:else}
  {#if data.embedded}
    <p class="meta">Nothing else to compare with in this scope. <a href={`/similar/${target.id}`}>Search everything</a>.</p>
  {:else}
    <p class="meta">This still has no embedding yet, so there is nothing to compare it with.</p>
  {/if}
{/if}

<style>
  .target {
    max-width: 22rem;
    margin: var(--s-3) 0 var(--s-2);
  }
  .target .frame {
    outline-color: var(--line-1);
  }
  .cards {
    gap: var(--s-3) var(--s-4);
    margin-top: var(--s-3);
  }
</style>
