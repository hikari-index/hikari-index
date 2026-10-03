<script>
  let { data, form } = $props();
  const bad = $derived(form?.problems ?? {});
  const sent = () => form?.values ?? {};
  // Same folder rules and labels as the add-a-file page.
  const fromShoko = (name) => data.shoko && /^\d+$/.test(name);
  const own = data.roots.names.filter((n) => !fromShoko(n));
  const folders = [...own, ...data.roots.names.filter(fromShoko)];
  const folderLabel = (name) => (fromShoko(name) ? `Shoko's folder ${name} (a folder Shoko manages)` : name);
  let root = $state(sent().root ? (data.roots.names.includes(sent().root) ? sent().root : "") : own.length === 1 ? own[0] : data.roots.names.length === 1 ? data.roots.names[0] : "");
  let path = $state(sent().path ?? "");
  const ready = $derived(data.roots.names.length > 0);
  const when = (t) => new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const said = (l) =>
    l.state === "committed" ? `${l.result?.videos ?? 0} video file${l.result?.videos === 1 ? "" : "s"}`
    : ["queued", "retry_wait", "leased", "running"].includes(l.state) ? "waiting for the worker"
    : l.state === "cancelled" ? "cancelled"
    : "could not be listed";
</script>

<svelte:head>
  <title>Add a folder — Hikari Index</title>
</svelte:head>

<nav class="crumbs"><a href="/admin/onboard">← Onboard</a> <a href="/admin/onboard/local">add one file instead</a></nav>

<h1>Add a folder of episodes</h1>
<p class="meta lead-narrow">For a season that is not in Shoko, or a gallery set up without it. Name the folder; the worker that reads the library lists the video files in it (that one folder, nothing else), and the next page shows them with a guessed episode number each for you to fix, untick and confirm. Nothing runs before you confirm.</p>

{#if form?.message}<p class="note" role="alert">{form.message}</p>{/if}

{#if !ready}
  <p class="note" role="status">No worker has reported a source folder yet, so there is nowhere to list from. The worker that reads the library reports the folders named in its <code>HIKARI_SOURCE_ROOTS</code> setting each time it checks in{data.roots.reported ? "; that setting is empty" : "; a worker from before this page reports none and needs updating"}.</p>
{/if}

<form method="POST" class="add">
  <fieldset disabled={!ready}>
    <legend>Which folder</legend>
    <label for="root">Source folder</label>
    <select id="root" name="root" bind:value={root} required aria-describedby="root-help">
      <option value="" disabled>Choose a source folder</option>
      {#each folders as name (name)}<option value={name}>{folderLabel(name)}</option>{/each}
    </select>
    <span class="why" id="root-help">The folders the worker has mounted, as its <code>HIKARI_SOURCE_ROOTS</code> setting names them.</span>
    {#if bad.root}<span class="why bad" role="alert">{bad.root}</span>{/if}

    <label for="path">Folder inside it <span class="muted">(leave empty for the source folder itself)</span></label>
    <input id="path" name="path" bind:value={path} maxlength="1000" autocomplete="off" spellcheck="false" class="mono wide" placeholder="Some Series/Season 1" aria-describedby="path-help" />
    <span class="why" id="path-help">Exactly as the worker sees it, capitals included. Either kind of slash works. Subfolders are not entered: name the folder the episode files are in.</span>
    {#if bad.path}<span class="why bad" role="alert">{bad.path}</span>{/if}
  </fieldset>
  <button type="submit" class="go" disabled={!ready}>List its files</button>
</form>

{#if data.recent.length}
  <h2>Recent listings</h2>
  <ul class="list">
    {#each data.recent as l (l.id)}
      <li><a href={`/admin/onboard/folder/${l.id}`}><span class="mono">{l.root}{l.path ? `/${l.path}` : ""}</span></a> <span class="muted">· {said(l)} · {when(l.createdAt)}</span></li>
    {/each}
  </ul>
{/if}

<style>
  .lead-narrow {
    max-width: 720px;
  }
  .add {
    max-width: 720px;
    margin-top: var(--s-4);
  }
  fieldset {
    border: 1px solid var(--line-1);
    padding: var(--s-3) var(--s-4) var(--s-4);
    margin: 0 0 var(--s-4);
  }
  fieldset > label {
    display: block;
    margin: var(--s-3) 0 var(--s-1);
  }
  .wide {
    width: 100%;
  }
  select {
    min-width: 12rem;
  }
  .why {
    display: block;
    color: var(--text-3);
    font-size: var(--t-meta);
    margin-top: var(--s-1);
  }
  .bad {
    color: var(--accent);
  }
  .muted {
    color: var(--text-3);
  }
  .list {
    margin: 0 0 var(--s-3);
    padding-left: 1.4em;
  }
  h2 {
    margin-top: var(--s-5);
  }
  .go {
    border-color: var(--accent);
    color: var(--text-1);
  }
</style>
