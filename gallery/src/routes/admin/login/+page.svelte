<script>
  let { data, form } = $props();
</script>

<svelte:head>
  <title>Sign in — Hikari Index</title>
</svelte:head>

<span class="label">Admin</span>
<h1>Sign in</h1>
<form method="POST" action={`?next=${encodeURIComponent(data.next)}`} class="login">
  <label>Name <input name="user" autocomplete="username" required /></label>
  <label>Password <input name="password" type="password" autocomplete="current-password" required /></label>
  {#if form?.message}<p class="err" role="alert">{form.message}</p>{/if}
  <button type="submit">Sign in</button>
</form>

<style>
  .label {
    margin-top: var(--s-4);
  }
  .login {
    display: grid;
    gap: var(--s-3);
    max-width: 22rem;
    margin-top: var(--s-4);
  }
  label {
    display: grid;
    gap: var(--s-1);
    color: var(--text-2);
    font-size: var(--t-ui);
  }
  button {
    justify-self: start;
  }
  .err {
    color: var(--accent);
    margin: 0;
    font-size: var(--t-ui);
  }
</style>
