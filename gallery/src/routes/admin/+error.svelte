<script>
  // An error under /admin keeps the admin bar (this renders inside the
  // admin layout), so a mistyped work id is one click from Review.
  import { page } from "$app/state";
</script>

<svelte:head>
  <title>{page.status === 404 ? "Not found" : "Something went wrong"} — Hikari Index</title>
</svelte:head>

<span class="label mono">{page.status}</span>
<h1>{page.status === 404 ? "There is nothing at this address." : "Something went wrong."}</h1>
<p class="meta">
  {#if page.status === 404}
    The episode or still may have been removed, or the link is wrong.
  {:else}
    {page.error?.message ?? "The page could not be built."}
  {/if}
</p>
<p class="crumbs">
  <a href="/admin">Review</a>
  <a href="/admin/jobs">Jobs</a>
</p>

<style>
  .label {
    margin-top: var(--s-6);
  }
</style>
