<script>
  // A palette strip: representative swatches, widths by weight. Pure
  // presentation; the palette shape comes from the import stage
  // (compactPalette in server/runs.js).
  let { palette, height = "10px", label = "palette" } = $props();
  const swatches = $derived(palette?.swatches ?? []);
  // An accent is a colour the frame draws the eye to, not one it is made
  // of; its weight is its true share, often a sliver, so it gets a floor
  // and a gap so it can be seen.
  const ACCENT_MIN = 0.06;
  const share = (s) => (s.a ? Math.max(s.w, ACCENT_MIN) : s.w);
  const total = $derived(swatches.reduce((a, s) => a + share(s), 0) || 1);
</script>

{#if swatches.length}
  <div class="strip" style:height role="img" aria-label={`${label}: ${swatches.map((s) => (s.a ? `${s.hex} (accent)` : s.hex)).join(", ")}`}>
    {#each swatches as s (s.hex)}
      <span class:accent={s.a} style:background={s.hex} style:flex={`${share(s) / total} 0 0`} title={s.a ? `${s.hex}, accent` : s.hex}></span>
    {/each}
  </div>
{/if}

<style>
  .strip {
    display: flex;
    width: 100%;
    overflow: hidden;
    margin-top: 6px; /* a sliver of page between frame and strip, so the strip never reads as part of the frame */
  }
  .strip span {
    display: block;
    min-width: 2px;
  }
  .strip .accent {
    margin-left: 3px;
  }
</style>
