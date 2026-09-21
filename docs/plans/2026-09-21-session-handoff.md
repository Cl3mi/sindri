# Session handoff — the `theoretical` parser fix is WRITTEN but NOT MEASURED

Written 2026-09-21. **Read `CLAUDE.md` first, then
`docs/plans/2026-09-17-session-handoff.md`, then this.** This does NOT supersede
the 2026-09-17 handoff — that one is still the state of play and carries the
device-setup steps. This is a narrow continuation note for one task in flight.

**One line:** §3's NEXT TASK is half done — the candidate parser edit is
committed and green at `874771c`, but the two CPU-seconds measurements that
decide whether it stays were **blocked on a machine with no client corpus**.
Pick this up where the corpus lives.

---

## 0. Why this handoff exists

The previous session ran on a machine that has the repo but **not** the client
data, **not** the agent's Bash guard, and **not** tesseract. Everything that
could be done without the corpus was done. Everything that needs it was not
attempted, rather than approximated.

---

## 1. What is committed

`874771c fix(parser): a boxed dimension is untoleranced, not a different type`

`parse_value` no longer forces `char_type = THEORETICAL` for
`hint == "theoretical"`. It classifies from the text (Ø → Diameter, R → Radius,
else Distance) through a new `_classify` helper extracted from the general path,
and **keeps the tolerance suppression**, which is the part the box actually
means.

Two consequences were carried in the same commit because the change is not
coherent without them:

* `targets._PREFIX` drops `"Theoretical"`. A gold row claiming that char_type
  now raises `UnrenderableRow("char_type")` instead of rendering a bare number
  that round-tripped only *through the defect being removed*.
* The `SHAPES` fixture in `tests/train/test_targets.py` asserted a
  `char_type="Theoretical"` gold row **the corpus never contains**. It is
  replaced by the two shapes `hint="theoretical"` really marks — a boxed
  `Distance` and a boxed `Diameter`. **Neither round-tripped before this
  change**, so boxed callouts were unrenderable as training targets as well as
  unscoreable as predictions. That was not in the registered plan; it is a
  second, free effect of the same fix and it should be stated when the arm is
  written up.

Verified on the writing machine: `_prompt_sha256()` still `aa7659f1929184ea`,
suite `882 passed, 3 skipped` plus the six tesseract failures described in §3.

---

## 2. THE NEXT TASK — finish the measurement

Both steps are **CPU seconds, no GPU**. Run them where
`$HOME/sindri-client-data` exists and the guard is installed.

### Step 1 — the baseline gate, with the parser AS IT WAS

The gate is `identical == n_pairs`: with an unmodified parser every stored field
must be reproducible, because that is where the stored fields came from. If it
does not hold, the offline reconstruction is broken and **no bound from step 2
means anything**.

Put the pre-change parser back without disturbing the commit:

```bash
git checkout 4500247 -- app/pipeline/parser.py
```

then run the sanctioned CLI, bare and unpiped:

```bash
python3 -m app.eval.runner score --run "$HOME/sindri-client-data/runs/r3-cropctx" --gold "$HOME/sindri-client-data/gold" --pdfs "$HOME/sindri-client-data/corpus/originals" --max-pages 1 --dpi 300 --exclude-clamped --splits "$HOME/sindri-client-data/meta/splits.json" --split dev --name reparse-probe --out "$HOME/sindri-client-data/reports/reparse-probe.report.json" --reparse-check
```

Restore the committed parser afterwards:

```bash
git checkout HEAD -- app/pipeline/parser.py
```

### Step 2 — the bound, with the change in place

The identical command with a different `--name` and `--out`
(`reparse-probe-theoretical`). `would_fix - would_break` is the bound.

### Step 3 — decide

**Bounded above at 4 of the 9 rows** — 2 have gold `Flatness`, 3 have no
mappable gold type — and a `char_type` fix only makes a row *fully correct* if
`nominal` and both tolerances already agree, so the real number is lower.

* Bound not positive → `git revert 874771c`. One commit, clean.
* Bound positive → **do not ship on the bound alone.** It still needs a scored
  run, and before proposing that run, answer the `r3-tallpad` question:
  **which taxonomy bucket are the fixed rows currently in?** Eight of the nine
  `theoretical` rows are `escaped_error`, which is the bucket that actually
  pays — unlike `r3-tallpad`, which fixed six rows the reviewer was already
  going to check and saved nothing.

---

## 3. Device state on the machine this was written on

Recorded so it is not re-diagnosed. **None of this is a code problem.**

| item | state |
|---|---|
| `core.hooksPath` → `hooks/` | installed |
| `~/.claude/sindri-doc-salt` | present |
| `~/.claude/hooks/sindri-guard.py`, `test-sindri-guard.sh` | **missing** |
| `~/.claude/sindri-protected-paths` | **missing** |
| `$HOME/sindri-client-data` | **empty** — no `meta/splits.json`, `probe --summary` on `corpus/originals` returns `n_docs: 0` |
| `tesseract` binary | **missing** |

Two things worth folding into the 2026-09-17 setup list:

1. **`tesseract` is a device prerequisite and is not listed.** Without it the
   suite reads `6 failed, 876 passed` — five `tests/eval/test_balloon_cv.py` and
   one `tests/eval/test_ingest.py`, all `TesseractNotFoundError` through
   `pytesseract`. A fresh operator will read that as broken code.
2. **A missing Bash guard is silent in exactly the way §1 warns about.** Commands
   naming the protected root simply run. Everything issued in that session was
   the sanctioned bare CLI, so nothing improper happened — but nothing would have
   stopped it. Verify `32 passed, 0 failed` before the corpus is present, not
   after.

The venv on that machine is `.venv/` and `python3` on `PATH` is **not** it —
`import fitz` fails. Use `.venv/bin/python3`, or activate first in a separate
call, since the guard refuses chained commands.

---

## 4. After that, unchanged from 2026-09-17 §4

1. Re-derive `weights.json` with the client — zero GPU, and now the deciding
   number three times over.
2. Re-measure the shipped crop win on the test split.
3. `gdt` — 17 rows at 0.1765, the largest non-dimension bucket, differently
   shaped confusion. The `theoretical` case was the clean one and is now
   answered either way.
4. Unblock route A serving, multi-page coverage, tiled rendering.
