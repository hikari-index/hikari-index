<script>
  import { enhance } from "$app/forms";
  import { formatTime, mediaUrl, placeholder } from "$lib/format.js";
  let { data, form } = $props();
  const KINDS = { text: "text on the frame", rating: "rating", unsure: "unsure scene label", scale: "unsure shot scale" };
  // Where a shot scale came from, in words (provenance.fields sources).
  const FROM = { scenery: "the scenery tag", "head-height": "the size of a head", tagger: "the tagger's tags", "face-occupancy": "the size of a face" };
  const query = (kinds, all) => {
    const q = new URLSearchParams();
    // The default set needs no k=; any other choice is spelled out.
    const isDefault = kinds.length === data.defaultKinds.length && data.defaultKinds.every((k) => kinds.includes(k));
    if (kinds.length && !isDefault) q.set("k", kinds.join(","));
    if (all) q.set("all", "1");
    const s = q.toString();
    return `/admin/exceptions${s ? `?${s}` : ""}`;
  };
  const toggled = (k) => (data.kinds.includes(k) ? data.kinds.filter((x) => x !== k) : [...data.kinds, k]);
  const action = (name) => `${query(data.kinds, data.all)}${query(data.kinds, data.all).includes("?") ? "&" : "?"}/${name}`;
  // What each reason says, in words: the evidence, not a verdict.
  function say(r) {
    if (r.k === "text") return `text: ${r.tags.map((t) => t.replaceAll("_", " ")).join(", ")}`;
    if (r.k === "rating") return `rating: questionable or explicit at ${r.score}`;
    if (r.k === "unsure") return `unsure scene label: ${r.label.replaceAll("-", " ")} at ${r.score}`;
    if (r.k === "scale" && r.head) return `unsure shot scale: ${r.label.replaceAll("-", " ")} from the size of a face, but the size of the head says ${r.head.replaceAll("-", " ")}`;
    if (r.k === "scale") return `unsure shot scale: ${r.label.replaceAll("-", " ")} at ${r.score}${FROM[r.source] ? `, from ${FROM[r.source]}` : ""}`;
    return r.k;
  }
  function marks(s) {
    const parts = [];
    if (s.review === "culled") parts.push("culled");
    if (s.review === "kept") parts.push("kept");
    if (s.locked) parts.push("locked");
    if (s.excluded) parts.push("hidden");
    return parts;
  }
  // Asked inside the enhancement: an onsubmit preventDefault does not stop
  // use:enhance, so a declined confirm there still queued every import.
  const rereadAfterAsking = ({ cancel }) => {
    if (!confirm("Read every work's run records again? Nothing you marked changes; one import per work goes through Jobs.")) {
      cancel();
      return;
    }
    return afterAction();
  };
  const afterAction = () => async ({ update }) => {
    await update({ reset: false });
  };
  const ratioOf = (stills) => {
    const t = stills[0]?.tiers.at(-1);
    return t && t.w && t.h ? `${t.w} / ${t.h}` : "16 / 9";
  };
</script>

<svelte:head>
  <title>Worth a look — Hikari Index</title>
</svelte:head>

<nav class="crumbs"><a href="/admin">← Review</a></nav>
<h1>Worth a look</h1>
<p class="meta intro">The picked stills the run records give a reason to check: text on the frame (credits, cards, subtitles), a rating the tagger did not call general, or a scene label proposed below the usual cut. Scene labels not listed here were proposed with the confidence review accepted nine times in ten; for other labels, not being listed here is no promise they are right. Reasons are evidence, not verdicts: a card is a still like any other until you cull or hide it. <strong>Unsure shot scale</strong> is off until you pick it: shot sizes guessed from the scenery tag or from the size of a head, tags that pointed two ways, or a face and its head that disagree; often a quarter to a half of a work's stills.</p>

<div class="top">
  <p class="filters" role="group" aria-label="Reasons">
    show:
    {#each Object.entries(KINDS) as [k, name] (k)}
      <a href={query(toggled(k), data.all)} class:on={data.kinds.includes(k)}>{name} <span class="mono">{data.counts[k] ?? 0}</span></a>
    {/each}
    <span class="sep"></span>
    <a href={query(data.kinds, !data.all)} class:on={data.all}>{data.all ? "reviewed included" : "unreviewed only"}</a>
  </p>
  <form method="POST" action={action("reread")} use:enhance={rereadAfterAsking}>
    <button type="submit" class="quiet">Read the run records again…</button>
    <span class="meta caption">Re-reads each work's run records so the reasons to look fill in: one import per work, through Jobs; nothing you marked changes.</span>
  </form>
</div>
{#if form?.message}<p class="note" role="status">{form.message}</p>{/if}

{#if !data.total}
  <p class="meta">Nothing to look at{data.all ? "" : " that is still unreviewed"}. {#if !data.groups.length && !data.all}If the works were imported before reasons existed, "Read the run records again" fills them in.{/if}</p>
{/if}

{#each data.groups as g (g.work.id)}
  <section>
    <h2><a href={`/admin/works/${g.work.id}`}>{g.work.label}</a> <span class="mono meta">{g.stills.length}</span></h2>
    <ol class="cards" style:--frame-ratio={ratioOf(g.stills)}>
      {#each g.stills as s (s.id)}
        <li class:dim={s.review === "culled" || s.excluded}>
          <a href={`/admin/stills/${s.id}`} aria-label={`edit the labels of the still at ${formatTime(s.ts)}`}>
            <span class="frame">
              <img style:background-color={placeholder(s)} src={mediaUrl(s.tiers[Math.min(1, s.tiers.length - 1)])} width={s.tiers.at(-1).w} height={s.tiers.at(-1).h} loading="lazy" alt={`${g.work.label}, still at ${formatTime(s.ts)}`} />
            </span>
            <span class="cap mono"><span class="time">{formatTime(s.ts)}</span></span>
          </a>
          <ul class="why">
            {#each s.reasons as r}<li class={r.k}>{say(r)}</li>{/each}
          </ul>
          <div class="controls">
            {#if marks(s).length}<span class="state">{marks(s).join(" · ")}</span>{/if}
            <form method="POST" action={action("review")} use:enhance={afterAction}>
              <input type="hidden" name="id" value={s.id} />
              {#if s.review === "culled"}
                <button name="state" value="unreviewed">restore</button>
              {:else}
                <button name="state" value="culled">cull</button>
                {#if s.review !== "kept"}<button name="state" value="kept">keep</button>{/if}
              {/if}
            </form>
            <form method="POST" action={action("flag")} use:enhance={afterAction}>
              <input type="hidden" name="id" value={s.id} />
              <input type="hidden" name="flag" value="excluded" />
              <button name="value" value={s.excluded ? "0" : "1"}>{s.excluded ? "show" : "hide"}</button>
            </form>
            <a class="edit" href={`/admin/stills/${s.id}`}>edit labels</a>
          </div>
        </li>
      {/each}
    </ol>
  </section>
{/each}

<style>
  .intro {
    max-width: 70ch;
  }
  .caption {
    display: block;
    max-width: 48ch;
    margin-top: var(--s-1);
  }
  .top {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2) var(--s-4);
    align-items: center;
    margin: var(--s-3) 0;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2);
    align-items: center;
    margin: 0;
    font-size: var(--t-meta);
    color: var(--text-3);
  }
  .filters a {
    color: var(--text-2);
    text-decoration: none;
    padding: 2px 6px;
    border: 1px solid var(--line-1);
  }
  .filters a.on {
    color: var(--text-1);
    border-color: var(--text-3);
  }
  .sep {
    width: 1px;
    height: 1em;
    background: var(--line-1);
  }
  section {
    margin-top: var(--s-5);
  }
  h2 {
    font-size: var(--t-body);
    margin: 0 0 var(--s-2);
  }
  h2 a {
    color: var(--text-1);
    text-decoration: none;
  }
  .cards {
    --card-min: 200px;
    gap: var(--s-4) var(--s-3);
  }
  .dim img {
    opacity: 0.35;
  }
  .why {
    list-style: none;
    margin: var(--s-1) 0 0;
    padding: 0;
    font-size: var(--t-label);
    color: var(--text-2);
  }
  .why .rating {
    color: var(--accent);
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-1);
    align-items: center;
    margin-top: var(--s-1);
    font-size: var(--t-label);
  }
  .controls form {
    display: inline;
  }
  .controls button {
    height: 24px;
    font-size: var(--t-label);
    padding: 0 6px;
  }
  .edit {
    color: var(--text-2);
    font-size: var(--t-label);
  }
  .state {
    color: var(--accent);
    font: 400 var(--t-label) / 1.4 var(--font-mono);
    width: 100%;
  }
</style>
