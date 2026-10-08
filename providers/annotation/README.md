# annotation

Bulk model proposals for a prepared bundle's stills: one proposal per
published candidate, using the label families and values defined in
`taxonomy.py` (`visual-taxonomy-v3`).

## Why this exists before any model

Every proposal carries all six label families and a score for each, so a
provider covering three families cannot emit anything on its own. This package is the join every later provider plugs into: it collects what
the available providers support and fills the rest with `abstain`.

That also converted three families from blank to backed with no new inference.
The colour descriptor already ran on every candidate and emitted hue-family
mass, weighted saturation, and luma percentiles; nothing mapped those numbers
onto taxonomy values, so the evidence was computed and discarded.

## Coverage today

| Family | Source | State |
|---|---|---|
| `lighting_color_character` | colour descriptor + tagger | backed; tagger adds `backlit`, `silhouette`, `monochrome` |
| `setting_time_weather` | tagger | backed once predictions are supplied |
| `shot_scale` | tagger | backed once predictions are supplied |
| `angle_composition` | tagger; spatial rules | `angle` backed by the tagger; `composition` by the rules in `composition_labels.py` (symmetry, thirds, a card rule), which speak on a third to a half of frames and abstain on the rest |
| `people` | face boxes fused with tagger count hints | backed; faces are a floor, disagreement flagged for review |
| `quality` | pipeline floor + framing distance | `usable` asserted, low score, routed to review |

The proposal CLI fuses palette, tagger and face results: pass `--tags` and `--faces` to `annotation.cli` and every family is backed. Each is optional and its families abstain when omitted.

### Backed is not the same as answered

All six families being backed means each one has a provider that *can* speak,
not that it does. Measured over 400 published frames from two episodes, the
share of frames where a field got a value rather than `abstain`:

| field | episode A | episode B |
|---|---|---|
| color_bias, saturation, people | 100% | 100% |
| setting | 63% | 36% |
| lighting | 30% | 30% |
| time | 28% | 18% |
| weather | 20% | 17% |
| shot_scale | 20% | 11% |
| angle | 2% | 2% |
| composition | 0% | 0% |

The colour and people half of the taxonomy is solved. The camera half — shot
scale, angle, composition — is the part this library exists to record and was
almost entirely empty, because the tagger has next to no framing vocabulary and
the zero-shot filler measured as no signal at all.

`shot_scale` is addressed (below) and `composition` has its rules (the table
above predates them). `angle` remains open.

### What the shot sizes mean

In film terms: `extreme-close-up` is a detail filling the frame;
`close-up` is a face or head filling most of it, with little or no
shoulder; `medium` runs from head and shoulders down to about the knees
(film's medium close-up, medium and medium long shots); `wide` is the whole
figure, just fitting or with plenty of room; `extreme-wide` is a place with
figures tiny or absent. Head and shoulders is medium, not close-up: a blind
grade of fresh works found a third of graded frames were head-and-shoulders,
the lanes already called most of them medium, and their face sizes overlap
close-ups' too much for any cut to separate the two.

### Shot scale resolves through lanes, not one signal

Face size is a strong signal and a blind one: it cannot see a frame without a
face, and 38–54% of published frames have none. So `shot_scale.py` runs a
cascade. Each lane either applies to a frame or does not, they are tried in
descending order of trust, and the first that applies answers.

| lane | applies when | measured share |
|---|---|---|
| tagger | the tagger named a scale outright | 20% / 11% |
| face occupancy | at least one face detected | 42% / 34% |
| scenery | tagged `scenery`, no face | 16% / 11% |
| head height | a head box, nothing above answered | (added later, below) |
| none — abstains | anything else | 22% / 44% |

Coverage went from 20% → **78%** (episode A) and 11% → **56%** (episode B)
before the head lane.

The lanes are deliberately not a vote, and every proposal records which lane
answered it under `provenance.shot_scale_lane` (and, for every label, its
source and score under `provenance.fields`). A single accuracy figure over a
cascade tells you nothing about which signal to fix; the reviewed sample has to be able
to score each lane separately.

The residue was mostly people shot from behind or too far away to detect a
face. The head lane reads it from an anime head detector
(`inference.detect_figures`), which sees the back of a head: the largest
head's height as a share of the frame's, cut at 0.19 and 0.72. On a
nine-work test set (1,800 frames) a head box covered 97% of detected faces,
head height ordered with face size (Spearman 0.95), and the cuts reproduced
the face lane's class on 83-86% of frames from works they were not fitted
on. It comes last, so it only fills frames the other lanes left empty (14%
of the test set), and changes no other lane's answer. On those fills it
agreed with the July reviewed sample on only 3 of 10 frames; two blind
grades of fresh works (shows the cuts were never fitted on) found it right
on 8 of 11 and 14 of 22 faceless frames it filled, several misses being
head and shoulders it called close-up. Right two times in three beats a
blank only when it is marked, so it scores low and the gallery lists these
sizes for review by default. The same head box gives composition a subject
position where no face was found; there the evidence is strong (its center
tracks the face center at Pearson 0.978 where both exist).

Two known limits, both measured rather than assumed. The face cut points sit at
the geometric mean between the medians of adjacent classes, which is defensible
placement and not calibration; `extreme-close-up` and `extreme-wide` have no
measured examples at all. And the scenery lane mislabels a close-up of a natural
subject as wide — a macro shot of wheat carries the same tags as a wheat field
seen from a distance, and no depth cue in the tagger's output separates them.
The lane keeps a low score and a review disposition for exactly this reason.

### A descriptor file is not evidence

The colour provider writes a short abstain record — `status`, `reason`, nothing
else — for a frame it cannot describe. That record is a well-formed file, so
`has_evidence` checks for the measurement blocks rather than for the file. One
such frame in 200 used to take down an entire episode's proposals with a bare
`KeyError: 'chroma'`.

The tagger runs: `providers/inference/tag_bundle.py` executes the pinned
checkpoint on a prepared bundle in the RTX worker image and emits general tags
and ratings. Character logits are never read -- the protocol requires them
discarded, and refusing to emit them closes the window that filtering downstream
would leave open.

## The tag cull

The tagger's vocabulary is fan-site vocabulary, not a product taxonomy.
Measured against `SmilingWolf/wd-swinv2-tagger-v3`:

| | count |
|---|---|
| tags in checkpoint | 10,861 |
| allowlisted as facet mappings | **66 (0.61%)** |
| discarded | 10,795 (99.39%) |

The allowlist (`wd_allowlist.json`) also keeps two groups of plain tags
beside the facet mappings, `things` and `actions`, which become a still's
tags rather than facet values.

2,751 of the discards are named characters, sliced out before anything is serialised. The bulk of the remainder describe
who is in frame — hair, eyes, clothing, expression — rather than how it is shot.
The checkpoint predicts **no copyright or series tags at all**, so the catalogue
remains the sole source of series metadata by construction rather than by policy.

Matching is exact tag name, never substring or pattern. This is a safety
property, not a style preference: on this vocabulary "bloom" catches `bloomers`,
"shadow" catches `eyeshadow`, and "dark" catches `dark_skin` and
`dark-skinned_female` — demographic descriptors the protocol forbids. Tests pin
each of those traps.

Count tags are read for arity only. The frequent count vocabulary is inherently
gendered, so `1girl` and `1boy` both yield `one` and the gender assertion is
dropped at this boundary. No gender value exists anywhere in the taxonomy, so
there is nowhere for such a label to go even if it were wanted.

People counting is additive, not a contest. `2girls` and `1boy` firing together
is three people; treating them as rival values undercounted every mixed group on
the first real run. A dedicated counter sums per-gender maxima and buckets the
total, `no_humans` gives `zero`, and `solo` settles the field outright. Gender is
read only to pick which counter a tag belongs to and is discarded immediately.

A `corroborating` tag cannot decide a field alone — `portrait` implies a
close-up only alongside a tag that names the value directly. Where two primary
tags disagree the score is damped by the disagreement, so a split decision reads
as uncertain and routes to review rather than passing as confident.

Abstention is deliberate and permanent design, not scaffolding. The protocol
demands an abstain state, and a proposal that guessed at families it has no
evidence for would corrupt review-by-exception: a reviewer accepting a
plausible-looking default is worse than one correcting an obvious blank.

## Colour bias reads hue mass, not Cb/Cr

Cb/Cr deviations shrink as a scene darkens. A dark orange interior measured
`cr_mean - cb_mean = +0.079` and would have been labelled `neutral`; on
hue-family mass the same frame reads `1.00` warm. Hue mass is weighted by pixel
count rather than intensity, so it survives low-key material.

Band 120 deg (green) counts as neither warm nor cool. It reads either way by
context, and forcing it into one group mislabels foliage.

## Quality is asserted, not measured

Published candidates have already cleared the pipeline's quality floor and its
redundancy test, so the only quality values still in play are `text-only-card`
and `prominent-text-overlay`. The composition rules recognise a text card from
the tagger's text evidence and the frame's entropy (`composition_labels.py`),
but the quality family does not yet use that: the proposal says `usable`, and
carries a deliberately low score with a `review` disposition so the
near-threshold rule sends the family to a human. Treat this family as
known-incomplete.

## Thresholds are provisional

Every cut point was chosen against 32 published frames from two titles. That is
enough to show the rules order real material correctly and nowhere near enough
to calibrate. The protocol requires per-label calibration on the reviewed sample, and
these values must be re-derived there before any metric is reported against
them. The `neutral` colour band in particular is untested: no frame in the
sample landed in it.

## Usage

```text
python -m annotation.cli \
  --bundle <prepared-bundle-dir> \
  --results <completed-colour-result-dir> \
  --out proposals.json \
  --run-ref run-<identifier>
```

Output is private. It derives from candidate pixels and carries the
work's identity, so it belongs beside the run and never in this
repository. Opaque IDs must match `^[a-z]+-[a-z0-9][a-z0-9-]{2,63}$`; the
emitter enforces that at construction rather than letting an unusable ID fail
at review-time validation.
