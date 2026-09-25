# gdt review UI — design

Written 2026-09-25. Approved in conversation before this file was written.
Supersedes the Markdown worksheet of `2026-09-22-gdt-review.md` §2 (its
decision table in §4 stands unchanged and is restated in §2 below).

---

## 1. The goal of the review

**The question.** 8 GD&T callouts on the dev split (shipped config) are right
in every field except their characteristic type, and on all 8 the type is a
GUESS: the parser found no GD&T symbol in the transcription and fell back to
Flatness. The review decides, per row, **where the fix lives**.

**Three facts per row** that no aggregate can supply, because they need the
drawing and gold's label (client content):

1. **What the drawing shows** — the characteristic the frame's symbol is.
2. **Did the symbol reach the transcription** — as a glyph (even a
   look-alike), as a word, or not at all.
3. **Does gold's label mean it** — does the inspection-sheet label name that
   same characteristic.

**Why it is worth doing.** At least 5 of the 8 are silent errors today: freeing
them is worth at least −1.67 review cost, the size of the shipped crop win
(−2.07). If the symbol reached the text and is merely unmapped, the fix is a
parser change priced in CPU seconds.

**Done means** all 8 rows answered and a counts-only tally at
`docs/eval/gdt-review-tally.json`, which an agent may read.

---

## 2. How to continue afterwards (registered 2026-09-22, unchanged)

| tally | next step |
|---|---|
| `freed_without_gpu` ≥ 3 | **Parser arm.** Add the symbol variants the review names to `parser._GDT_SYMBOLS`, plus synonym words the operator supplies for labels the scorer cannot read. Gate: `--reparse-check` identity 223/223 on the unmodified parser, then `would_break` = 0 and `would_fix` = `freed_without_gpu` row for row — a mismatch means review and parser disagree about a row, so stop and find out which. Then one scored run. |
| `freed_without_gpu` 1–2 | Same arm, shipped only if `would_break` = 0; written up as small (≤ −0.67). |
| `read_stage` is the majority | **No parser arm.** The reader drops GD&T symbols: record it as a concrete bucket for the next adapter's targets. Not a prompt (Rung 2 is closed without a mechanism). |
| `gold_disagrees_with_drawing` ≥ 2 | A gold-quality question for the client, raised with the `weights.json` re-derivation. |
| mostly `unsure` / blank | The review failed as a method. Say so; do not repeat it the same way. |

Registered prediction: the 2 Position and 1 Parallelism rows come back
`scorer_already_matches` if the drawing agrees with gold. No route is
predicted.

After this branch, the standing list resumes (2026-09-17 handoff §4):
re-derive `weights.json` with the client, re-measure the crop win on the test
split, unblock route A serving.

**Synonym words are client vocabulary.** The operator decides them. The map
already holds German gold words, so adding one is established practice; the
review only establishes which labels mean what.

---

## 3. What the operator sees

A local web app, one screen per row, about 30–60 s a row. No PDF to open, no
balloon to hunt for.

```
┌ gdt review · row 3 of 8 · ●●○○○○○○ ────────────────── saved ✓ ┐
│ Part …4711 · balloon 12 · upper left · SILENT ERROR today      │
│ ┌──── clean drawing, zoomed ────────────┐ ┌─ stamped ─────────┐ │
│ │  red box  = what the pipeline read    │ │  balloon 12 as    │ │
│ │  blue dot = gold balloon position     │ │  printed          │ │
│ └───────────────────────────────────────┘ └───────────────────┘ │
│ Inspection sheet: "…"        Reader transcribed: "…"            │
│ Pipeline predicted: Flatness (a guess: no symbol recognised)    │
│                                                                 │
│ 1  What does the frame show?                                    │
│    [⏤][⏥][○][⌭][⌒][⌓][∥][⊥][∠][⌖][◎][⌯][↗][⌰] [other] [unsure]  │
│ 2  Is that symbol in the transcription?   [G]lyph [W]ord [N]one │
│ 3  Does the sheet label mean it?          [Y]es  [N]o  [U]nsure │
│    note (stays on your machine) [__________________]            │
│  ← prev                                   next →  (Enter)       │
└─────────────────────────────────────────────────────────────────┘
```

* **Input.** Click, or keyboard: question 1 has a type-to-filter box
  (`pos` → Position, Enter confirms); questions 2 and 3 take single letters;
  Enter or → moves to the next row, ← back. Nothing is pre-selected.
* **Autosave** on every answer, shown as `saved ✓`. Closing the tab loses
  nothing; reopening resumes at the first unanswered row.
* **Finish** is always available. With rows unanswered it asks "N rows
  unanswered — finish anyway?"; those rows are reported as `incomplete`, never
  guessed. It shows the route summary and writes the tally, and the screen then
  says: tell the agent "done".
* **Hover** on a symbol button shows its name; a row whose drawing file is
  missing shows the text facts and says so instead of failing.

---

## 4. Components

Four units, each testable alone.

### 4.1 Deck — `app/eval/review.py` (reworked)

`score --review-deck PATH` replaces `--gdt-worksheet`. It writes one JSON file:

```
{ "version": 1, "kind": "gdt-char-type-only", "run": "<run name>",
  "originals_dir": "...", "stamped_dir": "...",
  "questions": [ {key, prompt, options: [{value, label, symbol?, hotkey?}]} ],
  "rows": [ {id: "g1", doc_id, balloon, where, gold_pt, pred_box_pt,
             gold_label, transcription, predicted, parser_defaulted,
             scorer_reads_gold_as, silent} ] }
```

* Row selection unchanged: `report.is_char_type_only` on `pred_kind == gdt`,
  grouped by document then balloon.
* `pred_box_pt` = the prediction's `target_region` ÷ `dump.scale` (render
  pixels → PDF points). `gold_pt` = the gold position, already in the
  originals' page space (gold is ingested with `--originals`).
* `stamped_dir` = the `stamped` sibling of `--pdfs` (the corpus layout
  `corpus/{originals,stamped}`, same stems).
* Questions live in the deck, so the page renders whatever the deck asks — a
  later review is a new deck, not new UI code. The routing logic in the tally
  is keyed by `kind`.
* Path rules unchanged: inside a protected root, never overwrite; checked
  before scoring. stdout: row count only.

### 4.2 Crops — `app/eval/review_crops.py`

`crop_png(pdf_path, rect_pt, overlays, zoom=3) -> bytes`, PyMuPDF only.

* Region = union of the pipeline box and a 10 pt square around the gold
  point, padded 40 pt, at least 160 × 100 pt, clamped to the page.
* Clean crop: red outline = pipeline box, blue dot = gold position. Overlays
  are drawn on an in-memory page and never saved to the file.
* Stamped crop: the same region with no overlays. It assumes the stamped and
  original sheets share page geometry, which holds on dev (`frame_mismatch`:
  15 of 15 frames agree); when the two page sizes differ, the crop is labelled
  "position approximate". If the gold point is missing, the row shows text
  facts only.
* Missing file or page → a clear error value; the server turns it into a
  placeholder image, never a stack trace.

### 4.3 Answers and tally — `app/eval/review.py`

* Answers file: `<deck>.answers.json` beside the deck —
  `{deck_sha, answers: {g1: {drawing_shows, symbol_in_transcription,
  gold_label_means_it, note}}, updated}`. `deck_sha` refuses answers made
  against a different deck.
* `tally(deck, answers)` keeps today's tested routing (`prediction_fix`,
  `gold_side`, `freed_without_gpu`, `incomplete`, `invalid`) and its
  values-blind guarantee: it reads only the three closed-vocabulary answers
  and the deck's closed-vocabulary `scorer_reads_gold_as`. Notes are never
  read.
* `review-tally <deck> --out <json>` stays as the headless path.

### 4.4 Server — `app/eval/review_server.py`, `runner review-serve <deck>`

Stdlib `http.server`, bound to `127.0.0.1`, port chosen by the OS unless
`--port` is given. Prints one URL carrying a random session token.

| route | does |
|---|---|
| `GET /?t=…` | the page — one inline HTML file, vanilla JS, inline CSS, no network |
| `GET /api/deck?t=…` | rows and questions |
| `GET /crop/<id>/<clean\|stamped>.png?t=…` | a crop, rendered on demand |
| `POST /api/answer?t=…` | save one row's answers (atomic write) |
| `POST /api/finish?t=…` | compute the tally, write it to `--tally-out` (default `docs/eval/gdt-review-tally.json`) |

Any request without the token → 403. Unknown row id → 404. The tally path
must NOT be inside a protected root (it is for the agent) — refused otherwise.
A relative `--tally-out` resolves against the directory `review-serve` was
started in, so the operator starts it from the repo checkout.

---

## 5. Data safety

The same boundary as today, tightened:

* Deck and answers hold client text and live only inside a protected root.
* The server listens on localhost only, and the session token stops other
  local processes and web pages from reading it.
* `review-serve` and `review-tally` stay **off** the agent guard's allowlist:
  the operator runs them. The agent generates the deck through the sanctioned
  `score` and sees only a row count. No guard change.
* The tally output is counts and row ids only. The existing leak test (SECRET
  planted in label, transcription, part number and note) carries over.
* No CDN, fonts or external requests: the page works offline and sends nothing
  anywhere.
* It is never a hosted page (no claude.ai artifact): it shows client data.

---

## 6. Testing

* **Deck:** row selection = the digest's rows; box conversion px → pt; stamped
  dir derivation; path refusal and no-overwrite (ported).
* **Crops:** geometry (union, padding, minimum size, clamping at page edges)
  as pure functions; one render on a synthetic PDF returns a PNG; missing file
  → error value.
* **Answers/tally:** save/load/resume; `deck_sha` mismatch refused; every
  existing tally test ported to the structured input, including the leak test.
* **Server:** the real handler on port 0 against a synthetic corpus — 403
  without token, deck served, crop served as PNG, answer persisted atomically,
  finish writes the tally, tally path inside a protected root refused, bind
  address is 127.0.0.1.
* **Not tested automatically:** the JavaScript's behaviour in a browser. It is
  kept small; the operator's first session is its test, and anything wrong
  there costs a reload, not data (autosave).

---

## 7. Out of scope

Multi-user review, editing gold, reviewing other buckets now (the deck makes
that a later config), and any change to the guard.

## 8. Migration

The Markdown worksheet path (`--gdt-worksheet`, `build_worksheet`, the
Markdown `tally`) is removed. The worksheet already generated on 2026-09-22
inside the protected root is simply unused; the operator may delete it.
`docs/plans/2026-09-22-gdt-review.md` §2 is updated to point here.
