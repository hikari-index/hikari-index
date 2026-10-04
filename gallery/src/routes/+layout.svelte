<script>
  import "../app.css";
  import { page } from "$app/state";
  let { data, children } = $props();

  // The footer line as text and link pieces. An address is http(s):// plus
  // at least one more character; sentence punctuation after it, and a
  // closing parenthesis with no opening one inside it, stay text. Svelte
  // escapes every piece, so nothing in the setting becomes markup.
  function linkify(text) {
    const parts = [];
    let at = 0;
    for (const m of text.matchAll(/https?:\/\/[^\s<>"]+/g)) {
      let url = m[0];
      for (;;) {
        if (/[.,;:!?'"]$/.test(url)) url = url.slice(0, -1);
        else if (url.endsWith(")") && url.split("(").length < url.split(")").length) url = url.slice(0, -1);
        else break;
      }
      if (!/^https?:\/\/[^/?#\s]/.test(url)) continue; // "https://" alone stays text
      if (m.index > at) parts.push({ text: text.slice(at, m.index) });
      parts.push({ text: url.replace(/^https?:\/\//, ""), href: url });
      at = m.index + url.length;
    }
    if (at < text.length) parts.push({ text: text.slice(at) });
    return parts;
  }
  const noticeParts = $derived(linkify(data.notice ?? ""));

  // Which top-level section the current path belongs to, for the header mark.
  const section = $derived.by(() => {
    if (page.error) return null; // an address that is nowhere marks nothing
    const p = page.url.pathname;
    if (p.startsWith("/series") || p.startsWith("/franchise") || p.startsWith("/still")) return "library";
    if (p.startsWith("/palette")) return "palette";
    if (p.startsWith("/composition")) return "techniques";
    if (p.startsWith("/admin")) return "admin";
    return "explore";
  });
</script>

<a class="skip" href="#main">Skip to content</a>
<header>
  <a class="brand" href="/">Hikari Index</a>
  <nav>
    <a href="/series" aria-current={section === "library" ? "page" : undefined}>Library</a>
    <a href="/" aria-current={section === "explore" ? "page" : undefined}>Explore</a>
    <a href="/palette" aria-current={section === "palette" ? "page" : undefined}>Palette</a>
    <a href="/composition" aria-current={section === "techniques" ? "page" : undefined}>Techniques</a>
    {#if data.adminLink}
      <!-- The owner's way in (HIKARI_ADMIN_LINK, on by default). The
           sign-in gate does not depend on it: this is a link, not a lock. -->
      <a href="/admin" class="admin-link" aria-current={section === "admin" ? "page" : undefined}>Admin</a>
    {/if}
  </nav>
</header>
<main id="main">
  {@render children()}
</main>
{#if data.notice}
  <footer class="notice">
    {#each noticeParts as part, i (i)}{#if part.href}<a href={part.href} rel="noopener">{part.text}</a>{:else}{part.text}{/if}{/each}
  </footer>
{/if}

<style>
  .skip {
    position: absolute;
    left: -999px;
    top: 0;
    background: var(--text-1);
    color: var(--bg-0);
    padding: 0.5rem 1rem;
  }
  .skip:focus {
    left: 0;
    z-index: 10;
  }
  header {
    display: flex;
    flex-wrap: wrap;
    gap: 0 var(--s-6);
    align-items: stretch;
    min-height: 56px;
    padding: 0 var(--gutter);
    border-bottom: 1px solid var(--line-1);
  }
  .brand {
    display: flex;
    align-items: center;
    font: 500 var(--t-label) / 1 var(--font-mono);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-1);
    text-decoration: none;
    white-space: nowrap;
  }
  nav {
    display: flex;
    flex-wrap: wrap;
    gap: var(--s-5);
  }
  nav a {
    display: flex;
    align-items: center;
    font-size: var(--t-ui);
    color: var(--text-2);
    text-decoration: none;
    border-bottom: 2px solid transparent;
    padding-top: 2px; /* keeps the text centered despite the underline */
  }
  nav a:hover {
    color: var(--text-1);
  }
  nav a[aria-current="page"] {
    color: var(--text-1);
    border-bottom-color: var(--accent);
  }
  /* Set apart from the public sections: after a gap, in the quieter color. */
  .admin-link {
    margin-left: var(--s-4);
    color: var(--text-3);
  }
  .notice {
    max-width: 1920px;
    margin: 0 auto;
    padding: var(--s-4) var(--gutter) var(--s-6);
    border-top: 1px solid var(--line-1);
    font-size: var(--t-meta);
    color: var(--text-3);
    overflow-wrap: anywhere; /* a long address must not widen a phone page */
  }
  .notice a {
    color: var(--text-2);
  }
  main {
    max-width: 1920px; /* raised from 1400 on 2026-09-27: an ultrawide was showing the 1400 grid with black margins */
    margin: 0 auto;
    padding: var(--s-4) var(--gutter) var(--s-7);
  }
</style>
