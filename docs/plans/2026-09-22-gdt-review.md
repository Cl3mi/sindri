# The gdt review — a 10-minute operator task, and what its answers decide

Written 2026-09-22, **before** any answer exists. The decision table in §4 is
registered here so the answers cannot be read into whatever we hoped for.

**One line:** 8 GD&T callouts are right in every field except their type, and
on all 8 the type is the parser's guess. Your answers to three closed questions
per row say whether the fix is a CPU-seconds parser/vocabulary change or a
read-stage problem. No GPU is needed to act on either answer.

---

## 1. What is already known (agent-safe counts, shipped config, dev split)

| fact | value | source |
|---|---|---|
| `gdt` rows wrong in `char_type` ONLY | **8** of 14 wrong gdt rows | `read_accuracy_by_kind.wrong_fields` |
| predicted type on those 8 | **Flatness, all 8** | `char_type_only_confusion` |
| type READ from a recognised symbol | **0 of 8** | `score --reparse-check` → `gdt_char_type_only` |
| type GUESSED (parser default) | **8 of 8** | same |
| scorer reads gold's label as Position | 2 | `char_type_only_confusion` |
| scorer reads gold's label as Parallelism | 1 | same |
| scorer finds no type word in gold's label | 5 | same |
| of the 8, reaching the customer silently | at least 5 (11 escaped among 14 wrong gdt rows) | `read_accuracy_by_kind` |

`parser._gdt_type` returns Flatness whenever it finds none of the symbols in
`parser._GDT_SYMBOLS`. On all 8 rows it found none. The reader got the
tolerance zone right on every one — that is what "char_type only" means — so it
read the frame; the question is what happened to the symbol.

**Worth**, at today's weights: each of these rows that is freed while it is a
silent error saves 5 ÷ 15 documents = **−0.33 review cost**. All 8 freed, at
least 5 of them silent: **at least −1.67**, the size of the shipped crop win
(−2.07).

---

## 2. What you do — about 10 minutes

> **Updated 2026-09-25:** the Markdown worksheet is replaced by a local review
> app (design: `2026-09-25-gdt-review-ui-design.md`). The questions, the
> routing and the decision table below are unchanged.

The deck is already generated (by `score --review-deck`, 2026-09-25) inside the
client-data folder, next to the reports.

1. **Start the app in your own terminal**, from the repo checkout:

   ```bash
   cd ~/mci/sindri/.claude/worktrees/eval-harness
   python3 -m app.eval.runner review-serve \
       "$HOME/sindri-client-data/reports/gdt-review.deck.json"
   ```

   It prints one link (`http://127.0.0.1:…/?t=…`). **Open it in your
   browser, and never paste it into an agent chat**: the `t=` part is the
   session token that keeps the page private.
2. **Answer each of the 8 rows** (about a minute each). Every row shows the
   clean drawing with a red box (what the pipeline read) and a blue dot (gold's
   balloon), the stamped drawing with the printed balloon, the inspection-sheet
   label and the transcription. Click a crop to enlarge it.

   | question | answers |
   |---|---|
   | 1 What does the frame show? | one of 14 ISO 1101 characteristics, or `other` / `unsure` — type to filter (`pos` → Position) |
   | 2 Is that symbol in the transcription? | `G`lyph (even a look-alike), `W`ord, `N`one |
   | 3 Does the inspection-sheet label mean it? | `Y`es, `N`o, `U`nsure |

   Letters answer the question marked in blue; `Enter` / `→` next row, `←`
   back. Clicking a chosen answer again clears it. Every click saves at once
   (top right shows `saved ✓`); you can close the tab and resume later. The
   note field is yours and never leaves your machine.
3. **Press Finish.** Unanswered rows are reported as unanswered, never guessed.
   It writes the counts-only tally to `docs/eval/gdt-review-tally.json` and
   shows the summary.
4. **Tell the agent "done".** Then stop the app with Ctrl+C.

Headless alternative: `python3 -m app.eval.runner review-tally <deck> --out
docs/eval/gdt-review-tally.json` computes the same counts from the saved
answers. The old `gdt-review.md` worksheet in the client-data folder is unused
and can be deleted.

If the deck ever needs regenerating, move the old one (and its
`.answers.json`) away first — `score` refuses to overwrite a deck, so answers
are never lost by accident.

---

## 3. How the tally turns answers into a route

Per row, two independent verdicts:

* **Prediction side** — `symbol_in_transcription`:
  * `glyph` / `word` → **parser**: the symbol reached the text and
    `_GDT_SYMBOLS` does not map it. A parser change, priced in CPU seconds by
    `score --reparse-check` exactly as the `theoretical` arm was.
  * `none` → **read_stage**: the reader dropped the symbol. Not a parser fix.
* **Gold side** — `gold_label_means_it`, against how the scorer already reads
  the label:
  * `yes` and the scorer already reads it as `drawing_shows` →
    **scorer_already_matches** (expected for the 2 Position / 1 Parallelism rows
    if the drawing agrees).
  * `yes` and the scorer finds no type word → **needs_synonym_word**: one entry
    in `normalize.CHAR_TYPE_SYNONYMS`. **You** decide the word, because it is
    client vocabulary; §3 of CLAUDE.md closed synonym entries on a premise that
    no longer holds (whole-label matching), and it is reopened only for words
    this review shows the label to mean.
  * `no` → **gold_disagrees_with_drawing**: a data question for the client.

`freed_without_gpu` counts rows whose prediction side is **parser** AND whose
gold side is **scorer_already_matches** or **needs_synonym_word** — both
CPU-only changes.

---

## 4. The decision, registered before the answers

| tally | next step |
|---|---|
| `freed_without_gpu` ≥ 3 | **Parser arm.** Add the variants the review names to `_GDT_SYMBOLS` (plus any synonym words the operator supplies). Gate: `--reparse-check` identity gate 223/223 on the unmodified parser, then `would_break` **must be 0** and `would_fix` must equal `freed_without_gpu` to the row. A mismatch means the review and the parser disagree about a row — stop and find out which, do not ship on the count. Then one scored run. |
| `freed_without_gpu` 1–2 | Same arm, but it is worth ≤ −0.67; ship only if `would_break` is 0, and say in the writeup it is small. |
| `read_stage` is the majority | **No parser arm.** The reader drops GD&T symbols. That is the Rung-3 read-stage target, and it adds a CONCRETE, countable bucket to the next adapter's targets — record it, do not reach for a prompt (Rung 2 is closed without a mechanism). |
| `gold_disagrees_with_drawing` ≥ 2 | Raise with the client alongside the weights re-derivation already on the list — it is a gold-quality finding, not a pipeline one. |
| mostly `unsure` / blank | The review failed as a method, not the pipeline. Say so; do not re-run it the same way. |

**No prediction is registered for which route wins.** Zero of 8 recognised
symbols is equally explained by a dropped symbol and by an unmapped look-alike,
and the review exists because the counts cannot tell them apart. What IS
registered: the 2 Position and 1 Parallelism rows are predicted to come back
`scorer_already_matches` if the drawing agrees with gold. If they do not, the
scorer's containment matching is reading those labels wrongly, which would be a
scoring finding in its own right.

**The damage counter is `would_break`**, and it is one the treatment can move —
the `theoretical` arm's reparse moved `identical` by exactly the rows it
touched — so it satisfies CLAUDE.md §4's rule that a gate must be able to
respond.

---

## 5. Data handling

* The deck and its answers are written only inside a protected root (enforced
  in `review.check_deck_path` / `save_answer`, tested) and the deck is never
  overwritten; answers are keyed to it by `deck_sha`.
* The app binds 127.0.0.1, checks the Host header, and refuses any request
  without the session token. It sends `no-store`, `no-referrer` and a CSP that
  forbids every external request, and loads no fonts or scripts from anywhere.
* The agent's guard was probed with simulated calls, no guard change: it DENIES
  the agent `review-serve`, `review-tally`, and `cat` / `Read` / `Grep` / `cp`
  of the deck or answers, and ALLOWS the sanctioned `score` that writes the
  deck. **It does not cover a plain `curl` to the running app** — the session
  token is what protects it, which is why the link must never be pasted to an
  agent.
* The tally reads only the three closed-vocabulary answers and the deck's own
  closed-vocabulary tag per row. A test plants `SECRET` in a label, a
  transcription, a part number and a note, and asserts none reaches the output.
