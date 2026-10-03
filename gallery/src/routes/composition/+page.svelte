<script>
  import { mediaUrl, placeholder, sentence } from "$lib/format.js";
  let { data } = $props();
  const pretty = (v) => String(v).replaceAll("-", " ").replaceAll("_", " ");
</script>

<svelte:head>
  <title>Techniques — Hikari Index</title>
</svelte:head>

<h1>Techniques</h1>
<p class="meta">How the stills are shot and lit, by name. Each tile opens the stills with that quality, where you can narrow further.</p>

{#each data.families as f (f.family)}
  <section aria-labelledby={`h-${f.family}`}>
    <h2 id={`h-${f.family}`}>{f.label}</h2>
    <ul class="tiles">
      {#each f.tiles as t (t.value)}
        <li>
          <a href={`/?${f.family}=${encodeURIComponent(t.value)}`} aria-label={`${pretty(t.value)}, ${t.count} stills`}>
            <span class="mosaic" class:one={t.covers.length === 1}>
              {#each t.covers as s (s.id)}
                <img style:background-color={placeholder(s)} src={mediaUrl(s.tiers[0])} width={s.tiers[0].w} height={s.tiers[0].h} loading="lazy" alt="" />
              {/each}
            </span>
            <strong>{sentence(pretty(t.value))} <span class="count">{t.count}</span></strong>
          </a>
        </li>
      {/each}
    </ul>
  </section>
{/each}
