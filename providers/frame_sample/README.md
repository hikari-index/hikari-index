# frame-sample

The deterministic frame sampler: the extraction stage of Hikari Index.

Selects a reduced set of candidate frame timestamps from a decoded source,
guided by PySceneDetect AdaptiveDetector scene-change scores, with OP/ED
exclusion interval handling and best-in-shot publishing.

No learned model. No raw media access in tests (synthetic fixtures only).

## Usage

```text
python -m frame_sample.cli --input video.mkv --output candidates.json \
  --exclusions exclusions.json --density 1.0 --adaptive-threshold 3.0 --seed 42
```

`--adaptive-threshold` sets the PySceneDetect adaptive-detector threshold and
also gates the per-shot extra samples. Frame extraction and dedup run only under
`--extract-frames`.

## Publishing policy

Sample count scales with shot length: `--density` multiplies a count derived
from `min_sample_spacing_seconds` (2 s), capped at `max_samples_per_shot` (3).
A flat per-shot count is what produced most near-twins in the 2026-07-23 runs —
a 1.5 s cut has only a 0.5 s sample range after boundary margins, so three
samples landed 80–200 ms apart and were the same picture by construction.

`pipeline_cli` then publishes **one frame per shot, plus a second only when its
framing genuinely differs**, capped by `--max-per-shot` (2):

- Shots are ranked, not frames. `scene_score` describes the cut that *opened* a
  shot, so every sample inside one shot carries the same value; ranking frames
  by it put a shot's samples adjacent at the top and published them as twins.
- Every sample in a shot is extracted and measured; the highest-detail one wins,
  with near-equal detail falling through to a shot-midpoint tiebreak.
- `--min-detail` / `--min-entropy` reject fades, black frames, and near-blank
  cards, which `scene_score` actively promotes (a pure black frame scored 124.4
  and topped a published sheet).
- `--intra-shot-distinct` / `--cross-shot-duplicate` compare downscaled pixel
  signatures. Perceptual hashing cannot do this job for animation: measured over
  32 real published frames, same-shot twins reached a pHash distance of 34 while
  unrelated frames sat at a median of 31. Pixel distance ordered the same frames
  cleanly — twins 0.060–0.149, genuinely different pairs from 0.198.

Per-shot outcomes, published-frame quality, and every omission with its reason
land in `audit/shot-coverage.json`, `audit/quality-omitted-candidates.json`, and
`audit/duplicate-omitted-candidates.json`.

Frame quality is audit evidence only. The v2.3 prepared-candidate bundle the
inference worker consumes is closed to additional candidate fields, so the
manifest is unchanged by this policy.

### Known limits

- The second-frame rule is at its resolution limit around 0.20: a genuinely new
  element entering a shot and a near-twin with one limb moved both measured
  ~0.201, so no threshold separates them. The rule errs toward publishing.
- Frames captured mid-aspect-transition (large uniform mattes) clear both the
  quality floor and the distinctness test. Detecting them needs a per-shot
  matte-consistency check, which does not exist yet.

The deployment image instead runs `frame_sample.pipeline_cli`. It writes a
prepared-candidate bundle containing exact-PTS rgb24 PNGs, then runs the
`palette-descriptor-0.2.0` color path and writes its checksum-bound candidate
result. Candidates held by proposed exclusions remain in `audit/` without
published pixel artifacts.

## Dependencies

| Package | Licence | Maintenance |
|---------|---------|-------------|
| `scenedetect[opencv]` >= 0.6.2 | BSD-3-Clause | Active (PyPI, 2024+) |
| `opencv-python-headless` (via scenedetect) | MIT / Apache-2.0 | Active |
| `imagehash` >= 4.3 | MIT | Active (PyPI, 2024+) |
| `Pillow` >= 10.0 | MIT-CMU | Active |
| `numpy` >= 1.24 | BSD-3-Clause | Active |

## Determinism

Same input video + same parameters + same exclusion intervals = identical
candidate set and provenance bytes. Wall-clock `created_at` is the only
non-deterministic field; pass `--created-at` to fix it.

## shot_id semantics

`shot_id` is a **sampling record**, not a boundary claim. It identifies the
detected-shot segment a candidate was drawn from (format: `shot-NNN`).
