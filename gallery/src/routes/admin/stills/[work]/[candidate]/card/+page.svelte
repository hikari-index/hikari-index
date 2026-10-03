<script>
  import { onMount } from "svelte";
  import { formatTime, mediaUrl } from "$lib/format.js";

  let { data } = $props();
  const s = $derived(data.still);
  const w = $derived(data.work);

  // Sizes people post at: Instagram portrait and square, and a 16:9 for
  // X / Bluesky / Mastodon.
  const SIZES = {
    portrait: { label: "Portrait 4:5", w: 1080, h: 1350 },
    square: { label: "Square 1:1", w: 1080, h: 1080 },
    landscape: { label: "Landscape 16:9", w: 1920, h: 1080 },
  };
  // Paper is the Hasselblad-export look; dark is the gallery's own.
  const MATTES = {
    paper: { label: "Paper", bg: "#f2efe8", text: "#1c1b19", soft: "#6f6b64", rule: "#d9d4ca", key: "#c9c3b8" },
    dark: { label: "Dark", bg: "#0b0b0d", text: "#e6e3dc", soft: "#8c8a84", rule: "#2a2a2e", key: "#3a3a3f" },
  };
  let size = $state("portrait");
  let matte = $state("paper");
  let withTones = $state(true);
  let withData = $state(true);

  let canvas = $state();
  let img = null;
  let ready = $state(false);

  const sub = $derived(
    [w?.episode, w?.episodeTitle, formatTime(s.ts)].filter(Boolean).join("  ·  "),
  );
  const dataLine = $derived([...data.facts, data.grade].filter(Boolean).join("  ·  ").toUpperCase());
  const filename = $derived(`${s.workId}-${s.candidate}-${size}.png`);

  function draw() {
    if (!canvas || !img) return;
    const Z = SIZES[size];
    const M = MATTES[matte];
    // 16:9 puts the frame on the left and the palette and text in a column
    // on the right; the taller sizes stack them under the frame.
    const wide = Z.w / Z.h > 1.4;
    const u = (wide ? Z.h : Z.w) / 1080; // layout unit: sizes below are for 1080
    canvas.width = Z.w;
    canvas.height = Z.h;
    const ctx = canvas.getContext("2d", { colorSpace: "srgb" });
    ctx.fillStyle = M.bg;
    ctx.fillRect(0, 0, Z.w, Z.h);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";

    const pad = 64 * u;
    const pal = s.palette;
    const aspect = img.naturalWidth / img.naturalHeight;
    const title = w?.title ?? s.workId;
    const mark = "HIKARI INDEX";
    const mono = (px) => `400 ${px * u}px "IBM Plex Mono", monospace`;

    function bar(x, y, width) {
      const total = pal.swatches.reduce((a, sw) => a + sw.w, 0) || 1;
      let bx = x;
      for (const sw of pal.swatches) {
        const bw = (sw.w / total) * width;
        ctx.fillStyle = sw.hex;
        ctx.fillRect(bx, y, Math.max(bw, 2 * u), 26 * u);
        bx += bw;
      }
    }
    // One lane: its name, then up to two chips with their hex, each in half
    // of the lane's width.
    function lane(name, list, x, y, width) {
      ctx.fillStyle = M.soft;
      ctx.font = mono(13);
      ctx.letterSpacing = `${1.5 * u}px`;
      ctx.fillText(name, x, y + 13 * u);
      ctx.letterSpacing = "0px";
      const chip = 40 * u;
      (list ?? []).slice(0, 2).forEach((sw, j) => {
        const chipX = x + j * (width / 2);
        const chipY = y + 26 * u;
        ctx.fillStyle = sw.hex;
        ctx.fillRect(chipX, chipY, chip, chip);
        ctx.strokeStyle = M.key; // a keyline, so a swatch the color of the matte still reads
        ctx.lineWidth = u;
        ctx.strokeRect(chipX + 0.5 * u, chipY + 0.5 * u, chip - u, chip - u);
        ctx.fillStyle = M.text;
        ctx.font = mono(14);
        ctx.fillText(sw.hex.toUpperCase(), chipX + chip + 10 * u, chipY + chip / 2 + 5 * u);
      });
    }
    const lanes = pal ? [["SHADOWS", pal.shadows], ["MIDTONES", pal.midtones], ["HIGHLIGHTS", pal.highlights]] : [];

    if (!wide) {
      const tonesH = withTones && pal ? 92 * u : 0;
      const textH = 44 * u + (sub ? 34 * u : 0) + (withData && dataLine ? 34 * u : 0);
      const below = 22 * u + (pal ? 46 * u : 0) + tonesH + 26 * u + textH;
      let fw = Z.w - 2 * pad;
      let fh = fw / aspect;
      if (fh > Z.h - 2 * pad - below) {
        fh = Z.h - 2 * pad - below;
        fw = fh * aspect;
      }
      // The frame and everything under it share one column, centered.
      const x = (Z.w - fw) / 2;
      let y = Math.max(pad, (Z.h - fh - below) / 2);
      ctx.drawImage(img, x, y, fw, fh);
      y += fh + 22 * u;
      if (pal?.swatches?.length) {
        bar(x, y, fw);
        y += 46 * u;
      }
      if (withTones && pal) {
        lanes.forEach(([name, list], i) => lane(name, list, x + (i * fw) / 3, y, fw / 3));
        y += tonesH;
      }
      ctx.fillStyle = M.rule;
      ctx.fillRect(x, y, fw, Math.max(1, u));
      y += 26 * u;
      ctx.fillStyle = M.text;
      ctx.font = `500 ${30 * u}px "IBM Plex Sans", sans-serif`;
      ctx.fillText(fit(ctx, title, fw * 0.78), x, y + 28 * u);
      ctx.fillStyle = M.soft;
      ctx.font = mono(13);
      ctx.letterSpacing = `${2 * u}px`;
      ctx.fillText(mark, x + fw - ctx.measureText(mark).width, y + 26 * u);
      ctx.letterSpacing = "0px";
      y += 44 * u;
      if (sub) {
        ctx.fillStyle = M.text;
        ctx.font = `400 ${19 * u}px "IBM Plex Sans", sans-serif`;
        ctx.fillText(fit(ctx, sub, fw), x, y + 18 * u);
        y += 34 * u;
      }
      if (withData && dataLine) {
        ctx.fillStyle = M.soft;
        ctx.font = mono(13);
        ctx.letterSpacing = `${1.5 * u}px`;
        ctx.fillText(fit(ctx, dataLine, fw), x, y + 16 * u);
        ctx.letterSpacing = "0px";
      }
      return;
    }

    // Wide: frame left, a column right, both centered on the card.
    const colW = 480 * u;
    const gap = 56 * u;
    let fw = Z.w - 2 * pad - colW - gap;
    let fh = fw / aspect;
    if (fh > Z.h - 2 * pad) {
      fh = Z.h - 2 * pad;
      fw = fh * aspect;
    }
    const x = (Z.w - fw - gap - colW) / 2;
    const top = (Z.h - fh) / 2;
    ctx.drawImage(img, x, top, fw, fh);
    const cx = x + fw + gap;
    let y = top;
    ctx.fillStyle = M.text;
    ctx.font = `500 ${30 * u}px "IBM Plex Sans", sans-serif`;
    for (const line of wrap(ctx, title, colW, 2)) {
      ctx.fillText(line, cx, y + 28 * u);
      y += 40 * u;
    }
    if (sub) {
      ctx.font = `400 ${19 * u}px "IBM Plex Sans", sans-serif`;
      for (const line of wrap(ctx, sub, colW, 2)) {
        ctx.fillText(line, cx, y + 20 * u);
        y += 28 * u;
      }
    }
    y += 22 * u;
    if (pal?.swatches?.length) {
      bar(cx, y, colW);
      y += 50 * u;
    }
    if (withTones && pal) {
      for (const [name, list] of lanes) {
        lane(name, list, cx, y, colW * 0.62);
        y += 84 * u;
      }
    }
    if (withData && dataLine) {
      ctx.fillStyle = M.soft;
      ctx.font = mono(13);
      ctx.letterSpacing = `${1.5 * u}px`;
      for (const line of wrap(ctx, dataLine, colW, 3)) {
        ctx.fillText(line, cx, y + 16 * u);
        y += 22 * u;
      }
      ctx.letterSpacing = "0px";
    }
    // The mark sits on the frame's bottom line.
    ctx.fillStyle = M.soft;
    ctx.font = mono(13);
    ctx.letterSpacing = `${2 * u}px`;
    ctx.fillText(mark, cx, top + fh);
    ctx.letterSpacing = "0px";
  }

  // Shorten text that would run past the column, with an ellipsis.
  function fit(ctx, text, max) {
    if (ctx.measureText(text).width <= max) return text;
    let t = text;
    while (t.length > 1 && ctx.measureText(t + "…").width > max) t = t.slice(0, -1);
    return t.trimEnd() + "…";
  }

  // Break text into at most `lines` lines that fit `max`; the last one is
  // shortened with an ellipsis if the text runs on.
  function wrap(ctx, text, max, lines) {
    const out = [];
    let line = "";
    const words = text.split(" ");
    for (let i = 0; i < words.length; i++) {
      const next = line ? `${line} ${words[i]}` : words[i];
      if (ctx.measureText(next).width <= max || !line) {
        line = next;
        continue;
      }
      out.push(line);
      line = words[i];
      if (out.length === lines - 1) {
        out.push(fit(ctx, words.slice(i).join(" "), max));
        return out;
      }
    }
    if (line) out.push(fit(ctx, line, max));
    return out;
  }

  function save() {
    canvas.toBlob((blob) => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    }, "image/png");
  }

  let failed = $state("");
  async function prepare() {
    failed = "";
    // The largest web image is the source; the card draws it smaller.
    const image = new Image();
    image.src = mediaUrl(s.tiers[s.tiers.length - 1]);
    try {
      await image.decode();
    } catch {
      failed = "the still's image could not be loaded";
      return;
    }
    // A font that fails to load is not fatal: the canvas falls back to the
    // browser's sans-serif, and the card is still a card.
    await Promise.allSettled([
      document.fonts.load(`500 30px "IBM Plex Sans"`),
      document.fonts.load(`400 19px "IBM Plex Sans"`),
      document.fonts.load(`400 13px "IBM Plex Mono"`),
    ]);
    img = image;
    ready = true;
  }
  onMount(prepare);

  $effect(() => {
    // Redraw whenever a choice changes.
    void [size, matte, withTones, withData, ready];
    draw();
  });
</script>

<svelte:head>
  <title>Post card · {w?.label ?? s.workId} at {formatTime(s.ts)} — Hikari Index</title>
</svelte:head>

<nav class="crumbs">
  <a href={`/admin/stills/${s.id}`}>← back to the still</a>
</nav>
<h1>Post card</h1>
<p class="meta intro">The frame on a matte with its palette, the shadows, midtones and highlights, and the show. Drawn here in your browser; Save gives you a PNG to post. Nothing is uploaded.</p>

<div class="controls">
  <p class="filters" role="group" aria-label="Size">
    {#each Object.entries(SIZES) as [k, z] (k)}
      <button type="button" class:on={size === k} aria-pressed={size === k} onclick={() => (size = k)}>{z.label} <span class="mono">{z.w}×{z.h}</span></button>
    {/each}
  </p>
  <p class="filters" role="group" aria-label="Matte">
    {#each Object.entries(MATTES) as [k, m] (k)}
      <button type="button" class:on={matte === k} aria-pressed={matte === k} onclick={() => (matte = k)}>{m.label}</button>
    {/each}
  </p>
  <label><input type="checkbox" bind:checked={withTones} /> shadows · midtones · highlights</label>
  <label><input type="checkbox" bind:checked={withData} /> data line</label>
  <button type="button" class="save" onclick={save} disabled={!ready}>Save PNG</button>
  {#if failed}<span class="meta" role="alert">{failed} · <button type="button" class="quiet" onclick={prepare}>try again</button></span>{/if}
</div>

<div class="stage" class:loading={!ready}>
  <canvas bind:this={canvas} aria-label={`Post card for ${w?.label ?? s.workId} at ${formatTime(s.ts)}`}></canvas>
</div>

<style>
  .intro {
    max-width: 680px;
  }
  .controls {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-2) var(--s-4);
    align-items: center;
    margin: var(--s-3) 0;
  }
  .controls .filters {
    margin: 0;
  }
  .filters button {
    background: none;
    border: 0;
    color: var(--text-2);
    padding: 2px 6px;
    cursor: pointer;
    font: inherit;
  }
  .filters button.on {
    color: var(--text-1);
    border-bottom: 1px solid var(--accent);
  }
  .save {
    margin-left: auto;
    padding: 6px var(--s-3);
    border: 1px solid var(--accent);
  }
  .stage canvas {
    display: block;
    max-width: 100%;
    max-height: calc(100vh - 14rem);
    width: auto;
    height: auto;
    outline: 1px solid var(--line-1);
  }
  .loading {
    min-height: 40vh;
    background: var(--bg-1);
  }
</style>
