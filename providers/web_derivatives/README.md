# derivatives

Encodes the web delivery ladder for a prepared candidate bundle: one AVIF
per tier per published candidate, plus a `derivatives-manifest.json` binding
every emitted file to its source artifact's checksum.

## Policy

`avif-q85-s4-444-lanczos-v1` — AVIF quality 85, speed 4, 4:4:4 chroma,
via Pillow/libavif, which writes the full CICP sRGB declaration. This is
the encode the project settled on after a codec comparison on real
frames (decision record 0006; the measurements are in the maintainer's notes).

Tier widths are 320 / 640 / 960 / 1920, each capped at the source width
(never upscale; duplicate widths collapse). The native-width tier re-encodes
the master's own pixels with no resampling — the exact case the quality
judgment was made on. Smaller tiers downscale with Lanczos first. The
top-down rule allows smaller tiers more aggressive settings later; doing so
is a new policy version, not a tweak.

## Execution surface

Pure Python over published bundle artifacts: no raw source, no model. In
production the raw-source worker image runs it as the web-images stage
(`worker/hikari_worker/stages.py`); it also runs from a host environment
whose Pillow has AVIF support (`PIL.features.check("avif")`).

## Usage

```bash
python -m web_derivatives.cli --bundle <prepared-bundle-dir> --out <dir> \
    --ffprobe <path-to-ffprobe> --verify-sample 8
```

Candidates are mapped through the bundle manifest's `artifact_name`, never
through filename order. With `--ffprobe`, emitted files are probed and any
declaration other than full-range sRGB/bt709 CICP is a hard failure — an
untagged or mis-tagged derivative is a defect regardless of its pixels.
