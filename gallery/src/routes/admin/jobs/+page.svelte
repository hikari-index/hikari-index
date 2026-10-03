<script>
  import { invalidateAll } from "$app/navigation";

  let { data, form } = $props();
  const when = (t) => new Date(t).toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const since = (t) => {
    const m = Math.max(0, Math.round((Date.now() - new Date(t).getTime()) / 60000));
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  };
  const GROUPS = [
    { key: "attention", title: "Needs you" },
    { key: "running", title: "Running" },
    { key: "waiting", title: "Waiting" },
    { key: "done", title: "Done" },
    { key: "cancelled", title: "Cancelled" },
  ];
  // A title filter over every group: matches the title, the episode name
  // or the work id. Client side; the page holds every chain.
  let q = $state("");
  const norm = (x) => (x ?? "").toLowerCase();
  const hit = (c) => !q.trim() || [c.title, c.episodeTitle, c.episodeTitleQueued, c.workId, c.removal?.label].some((t) => norm(t).includes(norm(q.trim())));
  // Newest first, except the queue, which reads in the order it will run.
  const grouped = $derived(
    Object.fromEntries(
      GROUPS.map((g) => {
        const list = data.chains.filter((c) => c.group === g.key && hit(c));
        return [g.key, g.key === "waiting" ? list.toReversed() : list];
      }),
    ),
  );
  // Done reads one row per work (its newest chain), with earlier runs of
  // the same work folded under it, so a season is twelve rows, not thirty.
  const doneByWork = $derived.by(() => {
    const byWork = new Map();
    for (const c of grouped.done) {
      if (!byWork.has(c.workId)) byWork.set(c.workId, { latest: c, earlier: [] });
      else byWork.get(c.workId).earlier.push(c);
    }
    return [...byWork.values()];
  });
  const busy = $derived(grouped.running.length + grouped.waiting.length > 0);

  // Waiting work, split by the machine that runs its next stage, in the
  // order that machine will take it. Each machine runs one stage at a time
  // and finishes it, so a ready stage waits for whatever the machine holds.
  const lines = $derived(
    data.machines
      .map((m) => ({
        ...m,
        chains: grouped.waiting
          .filter((c) => c.line?.capability === m.capability)
          .toSorted((a, b) => (a.line.position ?? 1e9) - (b.line.position ?? 1e9) || new Date(a.createdAt) - new Date(b.createdAt)),
      }))
      .filter((m) => m.chains.length),
  );
  const until = (t) => {
    const m = Math.max(1, Math.round((new Date(t).getTime() - Date.now()) / 60000));
    return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
  };
  const ordinal = (n) => n + (n % 10 === 1 && n % 100 !== 11 ? "st" : n % 10 === 2 && n % 100 !== 12 ? "nd" : n % 10 === 3 && n % 100 !== 13 ? "rd" : "th");
  function machineState(m) {
    if (!m.live) return "not checked in; its stages wait until it is back";
    if (m.busy.length) return m.busy.map((b) => `busy with ${b.workId}'s ${b.stage}${b.owner && b.owner !== m.capability ? ` on ${b.owner}` : ""}, ${since(b.startedAt)} so far`).join("; ");
    return "free; it takes the next stage within a minute";
  }
  // What a chain has already finished, so a row on its second stage reads
  // "palettes described · Add them to the gallery · ready, 4th in line"
  // and one on its first reads "Describe the palettes again · waiting…":
  // which half is done is otherwise only in the bar.
  function finishedText(c) {
    const finished = [];
    for (const s of c.stages) {
      if (s.id === c.current) break;
      if (s.state === "committed") finished.push(s.stage);
    }
    const said = { extract: "extracted", analyze: "analyzed", derive: "web images made", import: "added", palette: "palettes described", scrub: "files deleted", discard: "extra frames deleted" };
    return finished.map((st) => said[st] ?? `${st} done`).join(", ");
  }
  function done(c) {
    const f = finishedText(c);
    return (c.purpose ? `${c.purpose} · ` : "") + (f ? `${f} · ` : "");
  }
  // One line over Waiting when the same kind of chain is queued many
  // times over (a re-run of everything, a palette regeneration): how many
  // are part-way through and how many have not started, so nobody counts
  // rows to find out.
  // One line per kind of chain queued three or more times over (a re-run
  // of everything, a palette regeneration): how many wait at each stage,
  // counted from the stage each chain is actually on.
  function progressLines(list) {
    const groups = new Map();
    for (const c of list) {
      const k = c.purpose ?? "Onboarding";
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(c);
    }
    const out = [];
    for (const [k, cs] of groups) {
      if (cs.length < 3 || !cs.some((c) => c.stages.some((s) => s.state === "committed"))) continue;
      const at = new Map();
      for (const c of cs) {
        const s = c.stages.find((x) => x.id === c.current);
        const label = s ? s.label : "?";
        at.set(label, (at.get(label) ?? 0) + 1);
      }
      out.push(`${k}${q.trim() ? " (matching your search)" : ""}: ${[...at].map(([l, n]) => `${n} waiting at "${l}"`).join(" · ")}`);
    }
    return out;
  }
  function place(c, m) {
    const s = c.stages.find((x) => x.id === c.current);
    const name = done(c) + (s ? s.label : "");
    if (c.line?.retryAt) return `${name} · tries again in ${until(c.line.retryAt)}`;
    if (!c.line?.position) return `${name} · ${s?.waiting ? "waiting " + s.waiting : ""}`;
    const pos = c.line.position === 1 ? "next" : `${ordinal(c.line.position)} in line`;
    if (!m.live) return `${name} · ${pos}, once the ${m.label} checks in`;
    return `${name} · ready, ${pos}${m.busy.length && c.line.position === 1 ? " when the current stage finishes" : ""}`;
  }

  // One line for where a work stands: the stage it is on and why.
  function line(c) {
    const s = c.stages.find((x) => x.id === c.current);
    if (!s && c.removal) return c.finishedAt ? `removed (${c.removal.label}); files deleted ${when(c.finishedAt)}` : "";
    if (!s && c.discard) return c.finishedAt ? `review done; ${c.discard.deleted ?? 0} extra frames deleted (${Math.round((c.discard.bytes ?? 0) / 2 ** 20)} MB), ${c.discard.kept ?? 0} kept ${when(c.finishedAt)}` : "";
    if (!s && c.group === "cancelled") return `${c.purpose ? `${c.purpose} · ` : ""}cancelled${finishedText(c) ? ` after: ${finishedText(c)}` : ""}`;
    if (!s) return c.finishedAt ? (c.purpose ? `${c.purpose} · done ${when(c.finishedAt)}` : `added ${when(c.finishedAt)}`) : "";
    const tries = s.attempt > 1 || s.state === "dead_letter" || s.state === "retry_wait" ? ` · ${s.attempt} of ${s.maxAttempts} tries used` : "";
    const name = done(c) + s.label;
    // While it runs, the count is the try it is on, not tries used up, and
    // the usual range (from past runs of this stage on a video this long)
    // says whether a long wait is normal.
    if (s.state === "running" || s.state === "leased") {
      const kind = s.usual?.film ? "a film" : "an episode";
      const usual = !s.usual ? "" : s.usual.low === s.usual.high ? ` · usually about ${s.usual.low} min for ${kind} this long` : ` · usually ${s.usual.low} to ${s.usual.high} min for ${kind} this long`;
      return `${name} · ${s.owner ?? s.machine} · ${since(s.startedAt)}${usual}${s.attempt > 1 ? ` · try ${s.attempt} of ${s.maxAttempts}` : ""}`;
    }
    if (s.state === "cancel_requested") return `${name} · stopping`;
    if (s.state === "dead_letter") return `${name}: gave up${tries}`;
    if (s.state === "blocked") return `${name}: blocked, needs a human`;
    if (s.state === "ineligible") return `${name}: refused`;
    if (s.waiting) return `${name} · waiting ${s.waiting}${tries}`;
    return `${name} · ${s.state.replaceAll("_", " ")}`;
  }
  const segClass = (s) =>
    s.state === "committed" ? "ok"
    : ["running", "leased", "cancel_requested"].includes(s.state) ? "run"
    : ["dead_letter", "blocked", "ineligible"].includes(s.state) ? "bad"
    : s.state === "retry_wait" ? "wait"
    : "";

  // Keep the page current while anything is moving; stop when the tab is hidden.
  $effect(() => {
    if (!busy) return;
    const t = setInterval(() => {
      if (document.visibilityState === "visible") invalidateAll();
    }, 15000);
    return () => clearInterval(t);
  });
</script>

<svelte:head>
  <title>Jobs — Hikari Index</title>
</svelte:head>

<h1>Jobs</h1>
<p class="meta intro">Each onboarding runs in four stages, each on the machine that can do it; a stage starts when the one before it has finished. Each machine runs one stage at a time and finishes it before taking the next, so a ready stage can wait behind a long one; Waiting shows each machine's line. Waiting for the analyze worker is normal while it is off. New work starts from <a href="/admin/onboard">Onboard</a>.{busy ? " This page refreshes itself while work is moving." : ""}</p>

{#if form?.message}<p class="note" role="status">{form.message}</p>{:else if data.queued}<p class="note" role="status">Queued {data.queued} onboarding{data.queued === 1 ? "" : "s"}; {data.queued === 1 ? "it is" : "they are"} listed below.</p>{/if}

{#if !data.chains.length}
  <p class="meta" role="status">Nothing onboarded yet.</p>
{:else}
  <p class="find"><label><span class="sr">Find a title</span><input type="search" placeholder="Find a title, episode or work id" bind:value={q} /></label>{#if q.trim()}<span class="meta"> {GROUPS.reduce((n, g) => n + grouped[g.key].length, 0)} of {data.chains.length} onboardings</span>{/if}</p>
{/if}

{#snippet chainRow(c, why = null)}
  <li id={c.chainId} class="work">
    <div class="name">
      <span class="mono">{c.workId}</span>
      <span class="why">{why ?? line(c)}</span>
      {#each c.stages.filter((s) => s.errorMessage && (s.retryable || s.state === "dead_letter")) as s (s.id)}
        {@const first = s.errorMessage.split("\n")[0].slice(0, 160)}
        {#if s.errorMessage.trim() === first.trim()}
          <p class="err">{first}</p>
        {:else}
          <details class="err">
            <summary>{first}</summary>
            <pre>{s.errorMessage}</pre>
          </details>
        {/if}
      {/each}
    </div>
    <ol class="seg" aria-label="Stages">
      {#each c.stages as s (s.id)}
        <li class={segClass(s)} title={`${s.label}: ${s.state.replaceAll("_", " ")}`}><span class="sr">{s.label}: {s.state.replaceAll("_", " ")}</span></li>
      {/each}
    </ol>
    <div class="acts">
      {#each c.stages.filter((s) => s.retryable) as s (s.id)}
        <form method="POST" action="?/retry">
          <input type="hidden" name="stage" value={s.id} />
          <button type="submit">{s.state === "retry_wait" ? "Retry now" : "Retry"}</button>
        </form>
      {/each}
      {#if c.rerunnable}
        <form method="POST" action="?/rerun" class="rerun">
          <input type="hidden" name="work" value={c.workId} />
          <label class="sr" for={`b-${c.chainId}`}>Still count for the re-run</label>
          <select id={`b-${c.chainId}`} name="budget" value={c.budget}>
            {#each Object.entries(data.budgets) as [k, label] (k)}<option value={k}>{label}</option>{/each}
          </select>
          <button type="submit" title="Describe the frames and pick the stills again, then remake the web images; the extraction is reused">Re-run</button>
        </form>
      {/if}
      {#if !c.removal && !c.stages.some((s) => ["leased", "running", "cancel_requested"].includes(s.state))}
        <a class="quiet" href={`/admin/remove?scope=work&id=${c.workId}`}>Remove…</a>
      {/if}
      {#if c.group === "done" && !c.removal && !c.discard}
        <a class="quiet" href={`/admin/works/${c.workId}`}>Review stills</a>
      {/if}
      {#if c.cancellable}
        <form method="POST" action="?/cancel">
          <input type="hidden" name="chain" value={c.chainId} />
          <button type="submit" class="quiet">Cancel</button>
        </form>
      {/if}
    </div>
  </li>
{/snippet}

{#each GROUPS as g (g.key)}
  {@const list = grouped[g.key]}
  {#if list.length}
    {#if g.key === "done" || g.key === "cancelled"}
      <details class="group">
        <summary><h2>{g.title}</h2> <span class="meta">{#if g.key === "done"}{doneByWork.length} work{doneByWork.length === 1 ? "" : "s"}{#if doneByWork.length !== list.length}{" · "}{list.length} runs{/if}{:else}{list.length}{/if}</span></summary>
        {#if g.key === "done" && list.some((c) => c.rerunnable)}
          <form method="POST" action="?/rerunAll" class="bulk">
            <button type="submit">Re-run every finished work</button>
            <span class="meta">Describes the frames and picks the stills again, then remakes the web images, at each work's own still count. The extraction is reused; review marks stay.</span>
          </form>
        {/if}
        {#if g.key === "done"}
          <form method="POST" action="?/repaletteAll" class="bulk" onsubmit={(e) => { if (!confirm("Describe every work's palettes again with the current palette descriptor? Stills, picks, labels and review marks stay; only the palettes change.")) e.preventDefault(); }}>
            <button type="submit" class="quiet">Describe the palettes again</button>
            <span class="meta">Runs the palette descriptor over every still's master again (the source worker, then an import per work).</span>
          </form>
        {/if}
        {#if g.key === "done"}
          <ul class="works">
            {#each doneByWork as w (w.latest.chainId)}
              {@render chainRow(w.latest)}
              {#if w.earlier.length}
                <li class="earlier">
                  <details>
                    <summary class="meta">{w.earlier.length} earlier run{w.earlier.length === 1 ? "" : "s"} of {w.latest.workId}</summary>
                    <ul class="works">{#each w.earlier as c (c.chainId)}{@render chainRow(c)}{/each}</ul>
                  </details>
                </li>
              {/if}
            {/each}
          </ul>
        {:else}
          <ul class="works">{#each list as c (c.chainId)}{@render chainRow(c)}{/each}</ul>
        {/if}
      </details>
    {:else}
      <section class="group" aria-labelledby={`g-${g.key}`}>
        <div class="ghead">
          <h2 id={`g-${g.key}`}>{g.title} <span class="count">{list.length}</span></h2>
          {#if g.key === "waiting"}
            <form method="POST" action="?/cancelWaiting">
              {#each grouped.waiting as c (c.chainId)}<input type="hidden" name="chain" value={c.chainId} />{/each}
              <button type="submit" class="quiet" onclick={(e) => { if (!confirm(`Cancel the ${grouped.waiting.length} waiting chain${grouped.waiting.length === 1 ? "" : "s"} listed${q.trim() ? " (the ones matching your search)" : ""}? Finished and running work is not touched.`)) e.preventDefault(); }}>Cancel {q.trim() ? "the listed" : "all"} waiting ({grouped.waiting.length})</button>
            </form>
          {/if}
        </div>
        {#if g.key === "waiting"}
          {#each progressLines(list) as text (text)}<p class="meta mstate">{text}</p>{/each}
          {#each lines as m (m.capability)}
            <h3 class="machine">For the {m.label} <span class="count">{m.chains.length}</span></h3>
            <p class="meta mstate">{machineState(m)}</p>
            <ul class="works">{#each m.chains as c (c.chainId)}{@render chainRow(c, place(c, m))}{/each}</ul>
          {/each}
        {:else}
          <ul class="works">{#each list as c (c.chainId)}{@render chainRow(c)}{/each}</ul>
        {/if}
      </section>
    {/if}
  {/if}
{/each}

<style>
  .find {
    margin: 0 0 var(--s-3);
  }
  .find input {
    width: 22rem;
    max-width: 100%;
  }
  .earlier {
    list-style: none;
    margin: 0 0 var(--s-2) var(--s-4);
  }
  .earlier > details > summary {
    cursor: pointer;
  }
  .intro {
    max-width: 640px;
    margin-bottom: var(--s-4);
  }
  .group {
    margin-bottom: var(--s-5);
  }
  .ghead {
    display: flex;
    align-items: baseline;
    gap: var(--s-4);
  }
  .ghead form {
    margin-left: auto;
  }
  summary h2 {
    display: inline;
  }
  .count {
    color: var(--text-3);
    font-weight: 400;
  }
  .works {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .work {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 11rem auto;
    gap: var(--s-2) var(--s-4);
    align-items: center;
    padding: var(--s-2) 0;
    border-top: 1px solid var(--line-1);
  }
  .work:target {
    background: var(--bg-1);
  }
  .name {
    min-width: 0;
  }
  .why {
    display: block;
    color: var(--text-3);
    font-size: var(--t-meta);
  }
  .err summary,
  p.err {
    color: var(--text-3);
    font-size: var(--t-meta);
    overflow-wrap: anywhere;
  }
  .err summary {
    cursor: pointer;
  }
  p.err {
    margin: 0;
  }
  .err pre {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-size: var(--t-meta);
    max-height: 16rem;
    overflow: auto;
  }
  .seg {
    display: flex;
    gap: 3px;
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .seg li {
    flex: 1;
    height: 6px;
    border-radius: 3px;
    background: var(--line-1);
  }
  .seg .ok {
    background: var(--text-3);
  }
  .seg .run {
    background: var(--accent);
  }
  .seg .wait {
    background: repeating-linear-gradient(90deg, var(--accent) 0 3px, transparent 3px 6px);
  }
  .seg .bad {
    background: #d4544a;
  }
  .acts {
    display: flex;
    gap: var(--s-2);
    justify-content: flex-end;
  }
  .quiet {
    color: var(--text-3);
  }
  .machine {
    font-size: var(--t-ui);
    font-weight: 500;
    color: var(--text-1);
    margin: var(--s-4) 0 0;
  }
  .mstate {
    margin: 0 0 var(--s-1);
  }
  .rerun {
    display: flex;
    gap: var(--s-1);
  }
  .bulk {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--s-2) var(--s-4);
    margin: var(--s-2) 0 var(--s-3);
  }
  @media (max-width: 640px) {
    .work {
      grid-template-columns: minmax(0, 1fr) auto;
    }
    .seg {
      grid-column: 1 / -1;
      order: 3;
    }
  }
</style>
