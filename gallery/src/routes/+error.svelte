<script>
  import { page } from "$app/state";
</script>

<svelte:head>
  <title>{page.status === 404 ? "Not found" : "Something went wrong"} — Hikari Index</title>
</svelte:head>

<span class="label mono">{page.status}</span>
<h1>{page.status === 404 ? "There is nothing at this address." : "Something went wrong."}</h1>
<p class="meta">
  {#if page.status === 404}
    The still, episode or franchise may have been removed, or the link is wrong.
  {:else}
    {page.error?.message ?? "The page could not be built."}
  {/if}
</p>
<p class="crumbs">
  <a href="/series">Library</a>
  <a href="/">Explore</a>
  <a href="/palette">Palette</a>
  <a href="/composition">Techniques</a>
</p>

<style>
  .label {
    margin-top: var(--s-6);
  }
</style>
