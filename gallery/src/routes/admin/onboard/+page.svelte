<script>
  let { data } = $props();
</script>

<svelte:head>
  <title>Onboard — Hikari Index</title>
</svelte:head>

<h1>Onboard</h1>
<p class="meta lead-narrow">Pick a film, one episode or a whole season from Shoko. Nothing runs until you confirm on the last page, and then the work waits in <a href="/admin/jobs">Jobs</a> for the machines that do it.</p>
<p class="meta lead-narrow">A video that is not in Shoko, or no Shoko here? <a href="/admin/onboard/local">Add a file by its path</a>, or <a href="/admin/onboard/folder">add a folder of episodes</a>.</p>

{#if !data.configured}
  <p class="note" role="status">Shoko is not configured for this gallery, so there is nothing to search. To use it, the container needs <code>HIKARI_SHOKO_BASE_URL</code> and the read-only key file mapped, with <code>HIKARI_SHOKO_API_KEY_FILE</code> naming it.</p>
{:else}
  <form method="GET" class="search" role="search">
    <label class="sr" for="q">Series title</label>
    <input id="q" name="q" value={data.q} placeholder="Series title, as Shoko has it" autocomplete="off" required />
    <button type="submit">Search Shoko</button>
  </form>

  {#if data.error}
    <p class="note" role="alert">{data.error}</p>
  {:else if data.q && !data.results.length}
    <p class="meta" role="status">Shoko found nothing for “{data.q}”.</p>
  {:else if data.results.length}
    <table class="data">
      <thead><tr><th scope="col">Series</th><th scope="col">Episodes</th><th scope="col">Specials</th><th scope="col"></th></tr></thead>
      <tbody>
        {#each data.results as r (r.id)}
          <tr>
            <th scope="row"><a href={`/admin/onboard/series/${r.id}`}>{r.name}</a></th>
            <td>{r.episodes ?? "·"}</td>
            <td>{r.specials ?? "·"}</td>
            <td class="text">{r.inGallery ? "has stills" : ""}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
{/if}

<style>
  .lead-narrow {
    max-width: 640px;
    margin-bottom: var(--s-4);
  }
  .search {
    display: flex;
    gap: var(--s-2);
    max-width: 640px;
    margin-bottom: var(--s-4);
  }
  .search input {
    flex: 1;
  }
</style>
