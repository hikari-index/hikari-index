# How it works

Hikari Index makes a reference library of stills from video you already
have. You pick an episode, a film or a season; the tool pulls a few dozen
frames that stand for it, describes each one, and puts them in a gallery
you can browse by palette, composition, tags and likeness. Nothing is
uploaded anywhere, nothing runs on its own, and the video is never
written to.

## The parts

- **The gallery.** A web app on your network. Its public pages show the
  stills; its admin area (behind a sign-in) is where you choose what to
  onboard, watch the jobs, review the stills and fix labels. It owns the
  database and the job table, and it runs the last stage itself: adding
  finished stills to the library.
- **The worker that reads your video.** It runs on the machine that has
  the files. It takes the first and third stages of every job: finding
  the shots and pulling frames, and later making the web images. It is
  the only part that opens source video.
- **The analyze worker.** It runs the models: tags for what is in a
  frame, faces for how it is framed, an image embedding for likeness and
  mood search, a palette for each frame, and the picker that chooses the
  final set. It needs a GPU to be quick and a CPU to be possible. It
  never sees your source files, only the frames the first stage prepared.
- **The text encoder.** A small service that turns a typed phrase into
  the same kind of vector the frames carry, so "rainy neon street at
  night" finds frames. Optional; without it, tag search still works.
- **Shoko**, if you run it, is where the gallery looks up series,
  episodes and files, and how it groups them. It is read and never
  changed. Without Shoko you add files by path, one at a time or a folder
  at once, and type the title and episode numbers yourself.

## What happens to an episode

1. **You choose it.** From Shoko's list, or by naming a file or a folder.
   Nothing runs before you confirm.
2. **Shots and frames.** The worker checks the file against its recorded
   fingerprint (or takes one, for a file without Shoko), finds the shot
   boundaries, samples candidate frames across every shot, drops near
   twins, blanks and fades, and pulls the chosen frames exactly, by
   timestamp, with the pinned FFmpeg build. The frames go into a folder
   (a "bundle") with a list of what is in it, a checksum for every frame
   and a record of every setting.
3. **Describing and picking.** The analyze worker labels every candidate
   and picks the final set, spread evenly across the episode with variety
   inside each stretch. How many is your choice (fewer, balanced, more).
4. **Web images.** The worker encodes each picked frame as AVIF at up to
   four widths (never larger than the source), with the colour intent
   declared in the file.
5. **Into the library.** The gallery reads the records and adds the
   stills with their palette, labels, tags and vectors. From then on they
   show up in Library, Explore, Palette and Techniques.
6. **Review.** You cull what you do not want (credits, title cards),
   correct a label, pin a frame from the pool the picker did not take, or
   hide a still from the public pages. Openings and endings that repeat
   across a season are marked for you so one action culls them.
   [USING.md](USING.md) walks through it.

Everything keeps its provenance: which build made it, with which model
versions and settings. A re-run reuses the extraction and makes a new
pick without touching your review marks.

## The rules it keeps

There are three kinds, and they are not equally firm.

### Rules that protect your files and your library

Break one of these and something is lost or exposed. They do not bend.

- Shoko is read-only. The tool never changes anything in it.
- Source video is read-only and stays on your network. No cloud.
- Only the worker that mounts the video opens it. The analyze worker and
  the gallery get prepared frames and records, never the files.
- A stage finishes or it did not happen. Each stage writes into a staging
  folder and renames it into place, with a small "ready" file written
  last. A folder without one is ignored and made again.
- Every frame is checked against its checksum before anything reads it.
  The frames cross a network share between machines; a half-copied or
  changed file is refused, with a message that says which frame.
- A list of frames may only name files inside its own folder.
- The original frames (masters) are kept; the extra frames the picker did
  not use are deleted once you have finished reviewing, to give the disk
  back.
- Administration is for your network only. A copy you put in front of
  strangers is a read-only build with no admin area, no sign-in page and
  no form actions.

### Rules that keep old runs readable

Runs stay on disk for years and get read again (a re-pick, a re-import, a
new picker). These keep that working.

- Readers take what they need and ignore the rest. A stage reads the
  fields it uses from the files of the stage before, and a field it does
  not know is not an error. Adding a field never needs the other stages
  rebuilt.
- A field that is renamed is still read under its old name. The reader
  that cares maps old to new in one place (`identityOf` in
  `gallery/src/lib/server/runs.js` is the example).
- A stage refuses only what it cannot use, and says what to do about it:
  "this worker cannot read the bundle, update whichever is behind", not a
  format number.
- Everything records what made it: the build, the model versions, the
  settings. That is a note for a person, not a lock; nothing refuses a
  result because a build version differs. (One real exception: search
  vectors from a different embedding model cannot be compared with the
  stored ones, so the gallery refuses those.)

### Choices, not laws

These are what the tool was built to be. A fork can change them and
nothing breaks; it becomes a different tool.

- Nothing is scanned. Work starts from an explicit choice, never from a
  sweep of the library.
- Software FFmpeg is the reference for which frame is which, when it is,
  and what colour it is. Colour conversion follows the file's own tags;
  files with tag combinations the rules do not know are refused rather
  than guessed.
- The gallery shows what has been extracted, minus what you cull or
  hide. Reviewing is yours to do, and a still is visible on your network
  from the moment it is imported, so review before you show the gallery
  around. It is a curated subset, never a mirror of your library, and
  exposes no inventory of what you have.
- The models label scenes and framing. They do not identify characters or
  people, and they are trained on anime; on anything else the labels are
  unmeasured.
- Labels come from a fixed list with "unknown" and "abstain" in it. A
  model that has no evidence says so; it does not guess.

## What is on disk

One work is one folder under the runs share. Nothing in it is a secret
format; every record is JSON you can open.

```
<work>/extract/prepared/<date>--<title>--<episode>/
    manifest.json        the list: one entry per frame (id, file name,
                         size, checksum, timestamp) and how it was made
    READY.json           written last; holds the list's checksum
    README.txt           the same in words
    candidates/c0001--00h01m23s456.png ...
<work>/extract/results/completed/<bundle id>/
    artifacts/<frame id>.descriptor.json    the palette of each frame
<work>/extract/audit/    why each frame was kept or dropped
<work>/extract/surplus/  frames measured but not put in the bundle
<work>/analyze/          tags, faces, embeddings, labels, the pick
```

The code that writes and checks the bundle is one file,
`providers/frame_sample/bundle.py`. The code that reads it on the analyze
side is `load_and_verify_bundle` in `providers/inference/embed_bundle.py`.

## Changing it: two worked examples

**Add a field to the bundle.** Say you want each frame to carry the name
of the shot detector that found it.

1. In `providers/frame_sample/pipeline_cli.py`, add the key where each
   candidate's entry is built (search for `"observed_pts"`).
2. Read it wherever you want it: the gallery's import
   (`gallery/src/lib/server/runs.js`) reads the bundle's `manifest.json`
   directly. Use a default for runs made before your change, which will
   not have the field.
3. Rebuild the images whose code you touched (here the source worker and
   the gallery). The analyze worker does not read your field and does
   not need a rebuild; old and new bundles both go through it.

There is no schema file to update and no version number to raise.

**Add a label value.** Say you want a `dusk` value for time of day.

1. Add it to `TIME` in `providers/annotation/taxonomy.py`. That file is
   the one list of label families and values.
2. Make something propose it: the tagger mapping is
   `providers/annotation/wd_allowlist.json` (which tags mean which
   value), read by `providers/annotation/wd_labels.py`.
3. Rebuild the analyze worker. The gallery stores and shows the label
   text it is given, so it needs no change. Works analyzed before keep
   their old labels until you run Analyze on them again.
