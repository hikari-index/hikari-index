# Using the gallery

What to do once stills are in: finding things on the public pages, and
reviewing an episode in the admin area (culling, keeping, correcting
labels, pinning frames the picker passed over). Getting the stills in is
[SETUP.md](SETUP.md) and the README; what happens to an episode on the way
is [HOW_IT_WORKS.md](HOW_IT_WORKS.md).

The gallery has two halves. The **public pages** (Library, Explore,
Palette, Techniques) are what anyone on your network sees. The **admin
area** (the Admin link, behind your sign-in) is where you review. Nothing
you do on the public pages changes anything.

## Finding stills

### Library

Everything in the gallery, grouped the way Shoko groups it: a title, its
seasons, their episodes, in episode order. A work added by path is grouped
by the series title you typed. Open an episode to get its **sheet**: every
still in time order, numbered, with its palette strip and its main labels.
The arrows at the top and bottom walk to the episode before and after.

Only what you have stills for appears. The Library never lists episodes
you have not onboarded, so it shows nothing about the rest of your
collection.

### A still

Click any still to open it large, with:

- its palette: a strip of the colors it is made of, then its shadows,
  midtones and highlights as swatches with hex values. Click a swatch to
  see every still that carries that color.
- its labels (shot scale, setting, time of day, lighting and so on) and
  tags. Each one is a link to Explore narrowed to it.
- **Similar by image** and **Similar by palette** (below).

The left and right arrow keys move to the previous and next still of the
same episode.

### Explore

The front page. With nothing typed it shows a small sample from across
the library (it changes once a day) and a link to browse everything,
newest import first.

The search box reads what you type in one of two ways, chosen by the
toggle beside it:

- **by tag** (the default) matches what you type against the stills'
  tags: `rain` finds every still with a tag that contains "rain". The
  box takes one tag; add more from the rail. A word that is exactly a
  label value, like `night` or `low key`, is taken as that label, and the
  page says so ("night" read as time: night).
- **by mood** ranks stills by how the picture reads, from a description:
  "a lonely figure in a vast empty landscape", "rainy neon street at
  night". It needs the text encoder; when it is not running the page says
  so and offers the same words as a tag search. A tag search that finds
  nothing offers the reverse. With no encoder configured at all, the
  **by mood** toggle is greyed out.

Under the box, the **Narrow** rail (a sidebar on a wide window, a fold on
a narrow one) lists tags and label values with how many stills each would
leave. Click one to add it; click an active one, or its × above the
results, to remove it. **clear all** starts over. In mood mode the chips
narrow first and the closest 240 of what is left are shown.

Every state is in the address, so a search can be bookmarked or sent, and
Back undoes the last change. Press `/` anywhere on the page to jump to the
search box.

### Similar

From a still:

- **Similar by image** finds stills whose content and style are close:
  what is in the picture and how it is drawn, not its colors or framing.
- **Similar by palette** finds stills with the closest overall color
  balance, brightness and saturation, whatever is in them.

Both can be limited to everything, the same franchise, the same season or
the same episode.

### Palette

Browse by how a frame is graded. The page opens on **common grades**
(pairs like cool shadows, warm highlights), then two rows of swatches: the
colors the library's shadows go, and the colors its highlights go. Pick a
shadow, a highlight, or one of each; the counts on the swatches say what
each choice would leave.

**Browse by a single color** shows the sixty colors the library is most
made of. Pick one to see the stills that carry it, then add more colors to
narrow. **strict** wants each color close; **broad** accepts a wider
range.

### Techniques

One tile per named quality: shot scale, camera angle, composition, how
many people, lighting, time of day, weather and setting. A tile opens
Explore with that label set, where you can narrow further. A value shows
once at least five stills carry it; "unknown", "mixed" and "none
visible" never get a tile.

### What the labels are

The labels come from models trained on anime. They describe scenes and
framing; they never identify characters or people. Each one is a
proposal: when a model has no evidence it leaves the label out rather
than guess, and you can correct any of them (below). On anything that is
not anime the labels are unmeasured.

## Reviewing

A still is on the public pages from the moment it is imported. Review is
how you take out what should not be there (credits, title cards, a black
frame) and fix what the models got wrong. You do not have to look at
every still: the tools below let you cull what should go and accept the
rest in bulk.

Sign in through **Admin**. The admin bar has **Review**, **Worth a look**,
**Onboard**, **Jobs** and **Removed**.

### The marks

| Mark | What it means | On the public pages |
|---|---|---|
| **unreviewed** (to review) | a picked still nobody has marked yet | shown |
| **kept** | you looked at it and it stays; only a mark, and it stays editable | shown |
| **culled** | you threw it out | gone from every page; its address answers "not found"; **restore** brings it back as unreviewed |
| **hidden** | kept in the index, not shown (**hide**) | gone, as for culled; **show** brings it back |
| **locked** | a pin: a re-run must keep this frame in the set | no change |
| **corrected** | you changed a label or tag; your value shows in place of the model's and survives re-runs | your value shows |
| **not picked** | a still a re-run dropped that you had marked, or a pool frame you locked that waits for a re-run | not shown |
| **sensitive** | the tagger's rating reason (see Worth a look); a mark only | no change |

A still has one of unreviewed, kept or culled; locked, hidden,
corrected and sensitive sit alongside it.

Culled and hidden look the same to a visitor. The difference is for you:
a culled still counts as reviewed (you decided against it), a hidden one
is set aside without a verdict and does not count toward "to review".

A culled or hidden still's images can keep answering for up to half a
minute, and a browser that already loaded one keeps its copy.

### The Review page

Your library as a tree, the same grouping as the Library, with counts at
every level (picked, to review, repeats, culled, hidden, corrected) and a
bar for how much is reviewed (hidden stills count as reviewed there). **needs a look first** puts anything with stills to
review at the top, newest import first; **a–z** sorts by title. The box
filters by title or episode.

- **Continue reviewing** opens the next episode with unreviewed stills,
  showing only those.
- **Cull the repeats (N)** on a season: see Repeats below.
- **Mark the rest kept (N)** on an episode or a season marks every
  unreviewed still there as kept, including unreviewed ones you locked
  or corrected. Culled, hidden and already kept stills are not touched.
  It asks first, and the message after it has an **Undo** for that
  batch.
- **done reviewing…** and **remove…**: see the end of this page.

A typical pass over a new season: let the repeats check finish and cull
the repeats, open each episode and cull what else should go, then mark the
rest of the season kept.

### An episode's workbench

Click an episode on Review. Every picked still in time order, each with
its marks and its buttons: **cull** or **restore**, **keep**, **lock** or
**unlock**, **hide** or **show**, and **edit labels**. A button acts in
place; the page keeps your position.

The row of filters above the stills narrows the sheet: all, unreviewed,
culled, hidden, and when there are any, repeats, not picked, and the
pool.

To act on several stills at once, tick their boxes. Shift-click ticks a
run, which is how you take an opening or ending in one go. The bar that
appears does cull, keep, restore, lock, unlock, hide and show to every
ticked still.

Keys, when no field has focus (press `j` first to pick a still; the
other keys act on the outlined one):

| Key | Does |
|---|---|
| `j` / `k` | next / previous still |
| `space` | tick the current still (`shift` + `space` for a run) |
| `c` | cull it, or restore it if it is culled |
| `v` | keep it |
| `e` | open its label editor |
| `Esc` | clear the ticks |

**Mark the rest kept** is here too, for this episode only; it has no
Undo here, so set stills back with the bar's **restore**. The arrows
under the title walk the season, and the **episode title** fold lets you
type a title of your own; it shows instead of Shoko's and survives
re-runs. **Re-read from Shoko** fetches Shoko's title again, for an
episode it had no title for at the time.

### Correcting labels

**edit labels** (or `e`) opens the still's editor.

- **Labels:** each family shows the model's proposal. Leave it on **as
  proposed**, pick another value, pick **none** to clear it, or type a
  new value. A typed value becomes a choice for every other still.
- **Tags:** untick a tag to remove it; type more, separated by commas.
- **Review:** set unreviewed, keep or cull, lock it, hide it, and leave a
  note for yourself.

Your corrections sit beside the model's values rather than over them, so
a re-run with a new model still shows yours. Four buttons save: **Save
and next** (`Ctrl`+`Enter`; not on the last still), **Save and next
unreviewed**, **Save and stay** and **Save and back to the sheet**. `←`
and `→` walk without saving, `n` jumps to the next unreviewed still.
Leaving with unsaved changes asks first.

**make a post card**, at the top of the editor, draws the still on a
paper or dark matte with its palette and the show's name, in portrait,
square or 16:9, and saves a PNG. It is drawn in your browser; nothing is
uploaded.

### Worth a look

The picked, unreviewed stills the run records give a reason to check:

- **text on the frame:** the tagger saw credits, a title card, subtitles
  or a logo.
- **rating:** the tagger's scores for questionable and explicit added up
  to 0.10 or more. Some shows trip this on every episode; it is a prompt,
  nothing more.
- **unsure label:** a setting, time of day or weather label proposed with
  low confidence (the score is shown).

A reason hides nothing. The page has cull, keep and hide on each still,
and filters by reason; **unreviewed only** can be switched to include
reviewed stills. A typical session starts here, then moves to the
episodes. Works imported before these reasons existed have none until
you press **Read the run records again…**, which queues one import per
work on Jobs and changes none of your marks.

### Repeats

A few minutes after an import the gallery compares every episode of a
season with the others and marks stills whose frame appears elsewhere: an
opening, an ending, a studio logo, a recap. Nothing is culled or hidden
by the mark.

On Review, a season shows **checking for repeats…** until that is done,
then **Cull the repeats (N)**. That culls every unreviewed, unlocked still
that repeats in at least half of the season's other episodes (and in at
least two), and offers Undo. To keep one of them, lock it or keep it
first. Stills that repeat in fewer episodes (a recap, a reused shot) are
only marked; the workbench's **repeats** filter lists them. A season with
a single entry gets no marks, and one with two entries gets marks but
never the button (a frame can repeat in only one other episode there),
so cull those by hand. **repeats not checked (records
unreadable)** means an episode's run records could not be read; hover
for why.

## Getting a frame the picker passed over

The extraction keeps many more frames than the picker uses. They are the
**pool**, and you can bring any of them in.

1. On the episode's workbench, open **pool**. It lists the frames that
   never became stills, previewed from the originals (a page takes a
   moment the first time).
2. **lock** the ones you want. They now wait under **not picked**.
3. Open **Jobs**, open **Done**, find the episode, and press **Re-run**.
   It is on the episode's newest finished run, and only while nothing of
   it is running and its extra frames have not been discarded.

A re-run describes the frames and picks the stills again, then remakes
the web images. It reuses the extraction, so it does not read your video
again. Every locked frame is kept in the new set, and the frames you
locked from the pool get their labels and web images like the rest.

A re-run also changes how many stills an episode gets: choose **Fewer**
(about 60% of balanced), **Balanced** or **More** (about 150%, at most
300) beside the button. Your marks survive. A still the new set drops is
deleted if nobody touched it; if you had kept, culled, locked, hidden or
corrected it, it stays under **not picked** so the decision is not lost.
To bring one of those back, lock it and re-run again.

**Re-run every finished work** on Jobs does the same for every work
that can be re-run, each at its own still count, skipping the busy and
discarded ones. It does not ask first.

Locking a still that is already picked changes nothing on the public
pages. It keeps the still in the set at every re-run, and **Cull the
repeats** passes over it.

## When you are done with an episode

### Done reviewing: free the disk

The pool is most of an episode's disk use (about 1 GB for a 24-minute
episode, against about 150 MB once trimmed; [SETUP.md](SETUP.md) has the
figures). **done reviewing…** on an episode or season, offered once
nothing there is left to review (the workbench always links it, and the
page says what is in the way), shows how many extra frames would go and
how much space that frees, and deletes them when you confirm.

The picked stills, their full-size originals, any frame you locked and
every record stay. What goes is the choice: **after this the episode
cannot be re-run** with a different count or new pins, because the
picker could choose a frame that is gone. The way back is to remove the
episode and onboard it again, which loses your review of it. It is
refused while any frame you locked (from the pool, or under not picked)
still waits for its re-run.

### Removing a title, season or episode

**remove…** on Review (a title or season), **remove this episode…** on a
workbench, or **Remove…** on a Jobs row takes it out of the gallery and
deletes everything the pipeline made for it: its stills with your marks
and corrections, its job history, its web images and its run folder. Your
video is not touched. The confirm page lists what goes, with sizes, and
takes an optional reason. It is refused while a worker is running a stage
of it: cancel that on Jobs first.

**Removed** in the admin bar lists the newest fifty removals; every one
is recorded. To bring
something back, onboard it again; the pipeline runs from the start.

## Hiding the Admin link

The header's Admin link is on by default. Setting `HIKARI_ADMIN_LINK=0`
removes the link only; `/admin` typed into the address bar still reaches
the sign-in page. A copy for people outside your network should be the
read-only build instead, which has no admin area at all
([HOW_IT_WORKS.md](HOW_IT_WORKS.md), "Rules that protect your files and
your library").
