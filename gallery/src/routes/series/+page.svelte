<script>
  import { mediaUrl, placeholder } from "$lib/format.js";
  let { data } = $props();
  // Find a title as the list grows; matches the title, a season or an episode name.
  let q = $state("");
  const norm = (x) => (x ?? "").toLowerCase();
  const hit = (text) => !q.trim() || norm(text).includes(norm(q.trim()));
  const franchises = $derived(data.franchises.filter((f) => hit(f.name) || f.seasons.some((s) => hit(s.name) || s.works.some((w) => hit(w.label)))));
  const unsorted = $derived(data.unsorted.filter((w) => hit(w.label)));
  // Why a card is listed when its own name does not hold the words: the
  // season or episode that does, so the search does not look wrong.
  function matched(f) {
    if (!q.trim() || hit(f.name)) return null;
    const names = [];
    for (const s of f.seasons) {
      if (hit(s.name)) names.push(s.name);
      else {
        for (const w of s.works) {
          if (!hit(w.label)) continue;
          // The episode's own words when they hold the match; the whole
          // label when the match is in the work's title.
          const short = [w.episode, w.episodeTitle].filter(Boolean).join(" · ");
          names.push(short && hit(short) ? short : w.label);
        }
      }
    }
    if (!names.length) return null;
    return names.length > 2 ? `${names.slice(0, 2).join("; ")} and ${names.length - 2} more` : names.join("; ");
  }
  const plural = (n, word) => `${n} ${n === 1 ? word : word.endsWith("y") ? word.slice(0, -1) + "ies" : word + "s"}`;
</script>

<svelte:head>
  <title>Library — Hikari Index</title>
</svelte:head>

<h1>Library</h1>
<p class="meta">By franchise, then season, then episode, in order.</p>
{#if data.franchises.length + data.unsorted.length > 12}
  <p class="find"><label><span class="sr">Find a title or episode</span><input type="search" placeholder="Find a title or episode" bind:value={q} /></label>{#if q.trim()}<span class="meta found">{franchises.length + unsorted.length} of {data.franchises.length + data.unsorted.length}</span>{/if}</p>
{/if}
<ul class="tiles">
  {#each franchises as f (f.id)}
    {@const only = f.seasons.length === 1 && f.seasons[0].works.length === 1 ? f.seasons[0].works[0] : null}
    <li>
      <a href={only ? `/series/${only.id}` : `/franchise/${f.id}`}>
        <span class="cover">
          {#if f.cover}
            <img style:background-color={placeholder(f.cover)} src={mediaUrl(f.cover.tiers[0])} width={f.cover.tiers[0].w} height={f.cover.tiers[0].h} loading="lazy" alt={`${f.name}, first still`} />
          {/if}
        </span>
        <strong>{f.name}</strong>
        <span class="count">{plural(f.stills, "still")}</span>
        {#if matched(f)}<span class="match">matches {matched(f)}</span>{/if}
      </a>
    </li>
  {/each}
  {#each unsorted as work (work.id)}
    <li>
      <a href={`/series/${work.id}`}>
        <span class="cover">
          <img style:background-color={placeholder(work.cover)} src={mediaUrl(work.cover.tiers[0])} width={work.cover.tiers[0].w} height={work.cover.tiers[0].h} loading="lazy" alt={`${work.label}, first still`} />
        </span>
        <strong>{work.title}</strong>
        <span class="count">{#if work.episode}{work.episode}{#if work.episodeTitle}{" · "}{work.episodeTitle}{/if}{" · "}{:else if work.episodeTitle}{work.episodeTitle}{" · "}{/if}{plural(work.count, "still")}</span>
      </a>
    </li>
  {/each}
</ul>

<style>
  .find {
    margin: 0 0 var(--s-3);
  }
  .find input {
    width: 20rem;
    max-width: 100%;
  }
  .match {
    display: block;
    font-size: var(--t-meta);
    color: var(--text-2);
  }
  /* clear of the input's focus ring */
  .find .found {
    margin-left: var(--s-3);
  }
  .tiles {
    margin-top: var(--s-4);
    gap: var(--s-5) var(--s-4);
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  }
  .tiles strong {
    text-transform: none;
  }
</style>
