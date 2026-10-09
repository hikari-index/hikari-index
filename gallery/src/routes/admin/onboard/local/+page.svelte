<script>
  let { data, form } = $props();
  const WORK_ID_PATTERN = "[a-z0-9][a-z0-9\\-]{1,71}";
  const bad = $derived(form?.problems ?? {});
  // Every field is its own state, started from what the server sent back
  // (a POST here is a whole new page, so "started from" is enough, and the
  // server's own render is right with or without scripts). Fields fed
  // one-way from `form` were wiped whenever another field's state changed:
  // typing a title emptied the path.
  const sent = () => form?.values ?? {};
  // A folder named by a bare number is one Shoko manages: the worker maps
  // Shoko's own id for the folder to its mount, which is how a Shoko
  // onboarding finds its file. A bare "4" in a list explains nothing, so it
  // is labeled, and the operator's own folders come first. (Without Shoko
  // on this gallery a number is just a name someone chose.)
  const fromShoko = (name) => data.shoko && /^\d+$/.test(name);
  const own = data.roots.names.filter((n) => !fromShoko(n));
  const folders = [...own, ...data.roots.names.filter(fromShoko)];
  const folderLabel = (name) => (fromShoko(name) ? `Shoko's folder ${name} (a folder Shoko manages)` : name);
  // A fresh form starts on the operator's only own folder, or the only
  // folder there is. A returned folder is kept if it is still offered and
  // otherwise becomes "choose one": never another folder by default.
  let root = $state(sent().root ? (data.roots.names.includes(sent().root) ? sent().root : "") : own.length === 1 ? own[0] : data.roots.names.length === 1 ? data.roots.names[0] : "");
  let path = $state(sent().path ?? "");
  let kind = $state(sent().kind ?? "episode");
  let title = $state(sent().title ?? "");
  let episode = $state(sent().episode ?? "");
  let episodeTitle = $state(sent().episode_title ?? "");
  let group = $state(sent().group ?? "");
  let workId = $state(sent().work_id ?? "");
  let budget = $state(sent().budget ?? "balanced");
  // The series a typed title would join, for the hint under the field.
  const joins = $derived(data.catalogue.series.find((s) => s.name.toLowerCase() === title.trim().replace(/\s+/g, " ").toLowerCase()) ?? null);
  const ready = $derived(data.roots.names.length > 0);
</script>

<svelte:head>
  <title>Add a file without Shoko — Hikari Index</title>
</svelte:head>

<nav class="crumbs"><a href="/admin/onboard">← Onboard</a> <a href="/admin/onboard/folder">add a whole folder instead</a></nav>

<h1>Add a file without Shoko</h1>
<p class="meta lead-narrow">For a video that is not in Shoko, or a gallery set up without it. You say where the file is and what it is. Nothing is scanned or listed: the worker that reads the library looks at this one file when its turn comes, and until then nothing about it is known here, not its size, its length, or whether it is there at all. For a season's worth of files, <a href="/admin/onboard/folder">add the folder</a> and tick the episodes.</p>

{#if form?.queued}
  <p class="note" role="status">Queued {form.queued.label} as <span class="mono">{form.queued.workId}</span>. It waits in <a href={`/admin/jobs#${form.queued.chainId}`}>Jobs</a>. The form below is ready for the next file.</p>
{:else if form?.message}
  <p class="note" role="alert">{form.message}</p>
{/if}

{#if !ready}
  <p class="note" role="status">No worker has reported a source folder yet, so there is nowhere to add a file from. The worker that reads the library reports the folders named in its <code>HIKARI_SOURCE_ROOTS</code> setting each time it checks in{data.roots.reported ? "; that setting is empty" : "; a worker from before this page reports none and needs updating"}.</p>
{/if}

<form method="POST" class="add">
  <fieldset disabled={!ready}>
    <legend>Where the file is</legend>
    <label for="root">Source folder</label>
    <select id="root" name="root" bind:value={root} required aria-describedby="root-help">
      <option value="" disabled>Choose a source folder</option>
      {#each folders as name (name)}<option value={name}>{folderLabel(name)}</option>{/each}
    </select>
    <span class="why" id="root-help">The folders the worker has mounted, as its <code>HIKARI_SOURCE_ROOTS</code> setting names them.{#if folders.some(fromShoko)}{" "}A folder Shoko manages goes by Shoko's own number for it; you can add files from those folders here too.{/if}</span>
    {#if bad.root}<span class="why bad" role="alert">{bad.root}</span>{/if}

    <label for="path">Path inside it</label>
    <input id="path" name="path" bind:value={path} required maxlength="1000" autocomplete="off" spellcheck="false" class="mono wide" placeholder="Some Series/Season 1/Some Series - 01.mkv" aria-describedby="path-help" />
    <span class="why" id="path-help">The video file itself, exactly as the worker sees it, capitals included. Either kind of slash works.</span>
    {#if bad.path}<span class="why bad" role="alert">{bad.path}{#if form?.existing}{" "}<a href={`/admin/jobs#${form.existing}`}>See it in Jobs</a>.{/if}</span>{/if}
  </fieldset>

  <fieldset disabled={!ready}>
    <legend>What it is</legend>
    <div class="kinds" role="radiogroup" aria-label="Kind">
      <label><input type="radio" name="kind" value="episode" bind:group={kind} /> An episode of a series</label>
      <label><input type="radio" name="kind" value="movie" bind:group={kind} /> A film</label>
    </div>

    <label for="title">{kind === "movie" ? "Film title" : "Series title"}</label>
    <input id="title" name="title" bind:value={title} required maxlength="200" autocomplete="off" list="local-series" class="wide" aria-describedby="title-help" />
    <datalist id="local-series">{#each data.catalogue.series as s (s.name)}<option value={s.name}></option>{/each}</datalist>
    <span class="why" id="title-help">
      {#if joins}Joins “{joins.name}”, which is already here{joins.title !== joins.name ? `, under ${joins.title}` : ""}.
      {:else if kind === "movie"}The name the film is shown under.
      {:else}Episodes typed with the same title are one series: they sit together in the Library and are checked against each other for repeated openings and endings. A season is its own series; give each its own title.{/if}
    </span>
    {#if bad.title}<span class="why bad" role="alert">{bad.title}</span>{/if}

    {#if kind === "episode"}
      <label for="episode">Episode number</label>
      <input id="episode" name="episode" bind:value={episode} required inputmode="numeric" pattern={"\\d{1,4}"} autocomplete="off" class="num" />
      {#if bad.episode}<span class="why bad" role="alert">{bad.episode}</span>{/if}

      <label for="episode_title">Episode title <span class="muted">(optional)</span></label>
      <input id="episode_title" name="episode_title" bind:value={episodeTitle} maxlength="200" autocomplete="off" class="wide" />
      {#if bad.episode_title}<span class="why bad" role="alert">{bad.episode_title}</span>{/if}
    {/if}

    <label for="group">File it under <span class="muted">(optional)</span></label>
    <input id="group" name="group" bind:value={group} maxlength="200" autocomplete="off" list="local-titles" class="wide" aria-describedby="group-help" />
    <datalist id="local-titles">{#each data.catalogue.titles as t (t)}<option value={t}></option>{/each}</datalist>
    <span class="why" id="group-help">A wider title to gather several series or films in the Library, the way a show's seasons sit under the show. Left empty, a new series is its own title, and one already here stays where it is. Typed for a series already here, it moves the whole series.</span>
    {#if bad.group}<span class="why bad" role="alert">{bad.group}</span>{/if}

    <label for="work_id">Work id <span class="muted">(optional; the name in addresses and run folders)</span></label>
    <input id="work_id" name="work_id" bind:value={workId} pattern={WORK_ID_PATTERN} autocomplete="off" spellcheck="false" class="mono wide" placeholder="made from the title when left empty" />
    {#if bad.work_id}<span class="why bad" role="alert">{bad.work_id}</span>{/if}
  </fieldset>

  <fieldset disabled={!ready}>
    <legend>How many stills</legend>
    {#each Object.entries(data.budgets) as [key, b] (key)}
      <label class="choice"><input type="radio" name="budget" value={key} bind:group={budget} /><span>{b.label} <span class="muted">· {b.note}</span></span></label>
    {/each}
    {#if bad.budget}<span class="why bad" role="alert">{bad.budget}</span>{/if}
    <label class="hold"><input type="checkbox" name="hold_chapters" checked={sent().hold_chapters === "on" || sent().hold_chapters === true} /> <span>Hold the opening and ending, if the file has chapters for them <span class="muted">· the frames are kept in the pool, not picked; a chapter named OP or ED, or 85 to 95 seconds long near either end</span></span></label>
  </fieldset>

  <h2>What will run</h2>
  <ol class="list">
    {#each data.stages as s (s.label)}<li>{s.label} <span class="muted">· {s.machine}</span></li>{/each}
  </ol>
  <p class="meta lead-narrow">The first step checks the file before it reads it through: a path that is wrong, a file with no video, or one with more than one video stream stops there, and the row in Jobs says why. A file added this way gets its fingerprint then (a hash of the whole file), so the same folder and path can be queued only once, but the same video under another path is not noticed.</p>
  <p class="meta lead-narrow">Color is taken from the video's own color tags, as for every work. The labels (what is in a frame, how it is framed) come from models trained on anime and have not been measured on anything else.</p>

  <button type="submit" class="go" disabled={!ready}>Add it</button>
</form>

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
  fieldset > label:not(.choice) {
    display: block;
    margin: var(--s-3) 0 var(--s-1);
  }
  .kinds {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2) var(--s-5);
  }
  .kinds label {
    display: inline-flex;
    align-items: center;
    gap: var(--s-2);
  }
  /* A radio is as tall as any input here (app.css), so in a block label
     its text sat on the box's bottom edge, a line below the button. */
  .choice {
    display: flex;
    align-items: center;
    gap: var(--s-2);
  }
  .wide {
    width: 100%;
  }
  .num {
    width: 6rem;
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
  .go {
    border-color: var(--accent);
    color: var(--text-1);
    margin-top: var(--s-3);
  }
</style>
