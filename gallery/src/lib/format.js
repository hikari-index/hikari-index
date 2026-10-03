// Shared (server and browser) formatting helpers. Nothing here may touch
// the filesystem or env; that lives under $lib/server.

export function formatTime(seconds) {
  if (seconds == null) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rest = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
}

// A label as a heading: first letter up, the rest as written ("Rule of
// thirds"). CSS `capitalize` raises every word ("Rule Of Thirds").
export function sentence(text) {
  const s = String(text ?? "");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// A web image's address. `v` is the start of the file's sha256 (from the
// derivatives manifest), so the address changes when the file does and the
// browser can keep it for good (the media route marks versioned requests
// immutable).
export function mediaUrl(tier) {
  return `/media/${tier.src}${tier.v ? `?v=${tier.v}` : ""}`;
}

// The still's dominant color, painted in its image box until the image
// arrives, instead of the alt text.
export function placeholder(still) {
  return still?.palette?.swatches?.[0]?.hex ?? null;
}
