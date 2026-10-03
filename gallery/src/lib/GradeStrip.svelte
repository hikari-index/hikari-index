<script>
  // A still's tonal structure as one strip: shadows → midtones → highlights,
  // each lane two swatches. Reads left-to-right as "what happens in the
  // darks, the mids, the lights".
  let { palette, height = "10px", label = "shadows to highlights" } = $props();
  const lanes = $derived(
    palette ? [palette.shadows ?? [], palette.midtones ?? [], palette.highlights ?? []] : []
  );
  const all = $derived(lanes.flat());
</script>

{#if all.length}
  <div class="grade" style:height role="img" aria-label={`${label}: ${all.map((s) => s.hex).join(", ")}`}>
    {#each lanes as lane, i (i)}
      <div class="lane">
        {#each lane as s (s.hex)}<span style:background={s.hex} title={s.hex}></span>{/each}
      </div>
    {/each}
  </div>
{/if}

<style>
  .grade {
    display: flex;
    gap: 3px;
    width: 100%;
    margin-top: 6px;
  }
  .lane {
    flex: 1;
    display: flex;
    overflow: hidden;
  }
  .lane span {
    flex: 1;
    display: block;
  }
</style>
