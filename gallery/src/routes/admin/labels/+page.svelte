<script>
  let { data } = $props();
  const pretty = (v) => String(v ?? "").replaceAll("-", " ").replaceAll("_", " ");
  const pct = (x) => (x == null ? "–" : `${Math.round(x * 100)}%`);
  // Where each source's name comes from, in words.
  const SOURCES = {
    tagger: "the tagger's tags",
    "face-occupancy": "the size of the largest face",
    scenery: "the scenery tag",
    faces: "the face count",
    "tagger+faces": "the tagger's count, matched by the faces",
    palette: "the color measurements",
    mirror: "mirror symmetry",
    "face-position": "where the largest face sits",
    "interior-rule": "an interior with no weather tag",
    "not recorded": "a run from before sources were recorded",
  };
</script>

<svelte:head>
  <title>Label report — Hikari Index</title>
</svelte:head>

<nav class="crumbs"><a href="/admin">← Review</a></nav>
<h1>Label report</h1>
<p class="meta intro">
  How often your review agreed with the machine's labels, on the stills whose labels you marked checked in the still editor. A label left as proposed on a checked still counts as right; one you changed or cleared counts as wrong. Use it to judge a new model or cut-off on stills you have already reviewed before it touches the rest of the library. Shares from fewer than {data.enough} stills are dimmed: too few to re-tune anything on.
</p>

{#if !data.total}
  <p class="note">No still has its labels marked checked yet. In the still editor, tick "Labels checked" when you have looked at every label, then save.</p>
{:else}
  <p class="meta">
    {data.total} checked {data.total === 1 ? "still" : "stills"}, labeled by
    {#each data.runs as r, i (i)}{i ? "; " : ""}<span class="mono">{r.allowlist ?? "an older run"}</span> ({r.n}){/each}.
    <a href="/admin/labels/export.json" download>Download the checks</a> to score a new run against them.
  </p>

  {#each data.families as f (f.family)}
    <section aria-labelledby={`h-${f.family}`}>
      <h2 id={`h-${f.family}`}>{pretty(f.family)}</h2>
      <p class="meta">
        Proposed on {f.proposed} of {f.checked}: <strong class:few={f.proposed < data.enough}>{pct(f.agreement)}</strong> right, {f.corrected} changed, {f.cleared} cleared.
        No proposal on {f.missed + f.none}{#if f.missed}; you added one on {f.missed}{#if f.filled.length} ({#each f.filled.slice(0, 4) as x, i (x.value)}{i ? ", " : ""}{pretty(x.value)} {x.n}{/each}){/if}{/if}.
      </p>
      {#if f.values.length}
        <div class="scroll">
          <table class="data">
            <thead>
              <tr><th class="text">Source</th><th class="text">Proposed</th><th>Checked</th><th>Right</th><th class="text">Changed to</th></tr>
            </thead>
            <tbody>
              {#each f.values as v (v.source + "/" + v.value)}
                <tr>
                  <th class="text" scope="row"><span title={SOURCES[v.source] ?? ""}>{pretty(v.source)}</span></th>
                  <td class="text">{pretty(v.value)}</td>
                  <td>{v.n}</td>
                  <td class:few={v.n < data.enough}>{pct(v.agreement)}</td>
                  <td class="text">{#each v.to as x, i (x.value)}{i ? ", " : ""}{pretty(x.value)} {x.n}{/each}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        {#if f.sources.length > 1}
          <p class="meta">By source: {#each f.sources as s, i (s.source)}{i ? "; " : ""}{pretty(s.source)} <span class:few={s.n < data.enough}>{pct(s.agreement)}</span> of {s.n}{/each}.</p>
        {/if}
      {/if}
    </section>
  {/each}
{/if}

<style>
  .intro {
    max-width: 72ch;
  }
  section {
    margin-top: var(--s-5);
  }
  h2 {
    text-transform: capitalize;
    margin-bottom: var(--s-1);
  }
  .scroll {
    overflow-x: auto;
  }
  table.data {
    min-width: 520px;
  }
  .few {
    color: var(--text-3);
    font-weight: 400;
  }
  strong {
    font-weight: 600;
  }
</style>
