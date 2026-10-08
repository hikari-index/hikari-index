<script>
  let { data, children } = $props();
</script>

{#if data.admin}
  <nav class="admin" aria-label="Admin">
    <span class="label">Admin</span>
    <a href="/admin">Review</a>
    <a href="/admin/exceptions">Worth a look</a>
    <a href="/admin/labels">Labels</a>
    <a href="/admin/onboard">Onboard</a>
    <a href="/admin/jobs">Jobs</a>
    <a href="/admin/remove">Removed</a>
    <span class="who">signed in as <span class="mono">{data.admin.user}</span></span>
    <form method="POST" action="/admin/logout"><button type="submit">Sign out</button></form>
  </nav>
{/if}
<div class="dense">
  {@render children()}
</div>

<style>
  .admin {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-4);
    align-items: center;
    padding: var(--s-2) var(--s-3);
    margin: 0 0 var(--s-4);
    background: var(--bg-1);
    border-left: 2px solid var(--accent);
    font-size: var(--t-meta);
  }
  .admin a {
    color: var(--text-2);
  }
  .who {
    color: var(--text-3);
    margin-left: auto;
  }
  .admin form {
    display: inline;
  }
  .admin button {
    height: 28px;
    padding: 0 var(--s-3);
    font-size: var(--t-meta);
  }
  /* admin is the same system one spacing step down */
  .dense {
    --card-min: 150px;
    font-size: var(--t-ui);
  }
  .dense :global(input:not([type="checkbox"]):not([type="radio"])),
  .dense :global(select),
  .dense :global(button) {
    height: 32px;
    font-size: var(--t-ui);
    padding: 0 var(--s-2);
  }
</style>
