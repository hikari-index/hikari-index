<script>
  import { mediaUrl, placeholder } from "$lib/format.js";
  let { data } = $props();
  const f = $derived(data.franchise);
  const plural = (n, word) => `${n} ${n === 1 ? word : word.endsWith("y") ? word.slice(0, -1) + "ies" : word + "s"}`;
  const year = (d) => (d ? String(d).slice(0, 4) : "");
</script>

<svelte:head>
  <title>{f.name} — Hikari Index</title>
</svelte:head>

<nav class="crumbs"><a href="/series">← Library</a></nav>
<h1>{f.name}</h1>
<p class="meta mono">{plural(f.stills, "still")}</p>

<ol class="seasons">
  {#each f.seasons as s (s.id)}
    <li>
      <h2>
        {s.name}
        {#if s.type || s.airDate}<span class="sub">{[s.type, year(s.airDate)].filter(Boolean).join(" · ")}</span>{/if}
      </h2>
      <ul class="tiles">
        {#each s.works as w (w.id)}
          <li>
            <a href={`/series/${w.id}`}>
              <span class="cover">
                <img style:background-color={placeholder(w.cover)} src={mediaUrl(w.cover.tiers[0])} width={w.cover.tiers[0].w} height={w.cover.tiers[0].h} loading="lazy" alt={`${w.label}, first still`} />
              </span>
              <strong>{#if w.episode && w.episodeTitle}{w.episode}{" · "}{w.episodeTitle}{:else if w.episode}{w.title}{" · "}{w.episode}{:else}{w.episodeTitle ?? w.title}{/if}</strong>
              <span class="count">{plural(w.count, "still")}</span>
            </a>
          </li>
        {/each}
      </ul>
    </li>
  {/each}
</ol>

<style>
  .seasons {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  h2 .sub {
    margin-left: var(--s-3);
    text-transform: none;
    letter-spacing: 0;
    font-family: var(--font-ui);
    font-weight: 400;
  }
  .tiles strong {
    text-transform: none;
  }
</style>
