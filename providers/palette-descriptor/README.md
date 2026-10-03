# palette-descriptor

Deterministic colour descriptor CLI for anime candidate stills.

Ported from the maintainer's image2lightroom project (`palette.rs` + scope-core color/decode), MIT, 2026-07-19. Lightroom preset-generation code intentionally excluded.

## Usage

```text
palette-descriptor <input.png> <output.json> [--max-samples N]
```

- `input.png`: candidate still, PNG, rgb24
- `output.json`: v1 colour-descriptor JSON payload
- `--max-samples N`: maximum pixels to analyze (default 4,000,000)

Exit 0 on success; non-zero with a one-line stderr message on failure.

## Dependencies

| Crate | Version | Licence | Maintenance |
|-------|---------|---------|-------------|
| `image` | 0.25 (png only, default-features off) | MIT | Active (crates.io, 2025+) |
| `serde` | 1 | MIT/Apache-2.0 | Active |
| `serde_json` | 1 | MIT/Apache-2.0 | Active |

## Percentile method

Luma percentiles (p05, p50, p95) use nearest-rank over sorted sample luma values.

## Bucket sizes

Dominant and representative palettes hold up to 8 colours by area; the representative palette may carry up to 2 accents on top (`accent: true`: a saturated colour spread too thin for any one bin to clear the floor, unlike every colour picked by area; shown, not counted in the hue, chroma and temperature statistics). Tone buckets (shadows, midtones, highlights) hold up to 4.
