# Session handoff — where the quality is, and how to pick this up anywhere

Written 2026-09-17. **Read `CLAUDE.md` first, then this.** Supersedes
`docs/plans/2026-09-16-session-handoff.md` entirely; every older handoff is one
or more policies behind.

**One line:** the campaign's central finding is settled and acted on — the INPUT
to the read holds the remaining quality — and the sharpest routing signal yet
says **15% of rows carry 41% of the silent errors, and they cannot score correct
at all.** The next move is a CPU-seconds measurement, not a GPU night.

> **Update 2026-09-22: §3's NEXT TASK is DONE and CLOSED.** The `theoretical`
> fix was measured with `--reparse-check`: gate 223/223, bound
> **`would_fix 0, would_break 0`**, reverted at `89e0375`. Those rows are wrong
> in their values as well, so the "wrong by construction" reading below was
> half the story. Do not redo §3; go straight to §4 item 1. Result:
> `docs/plans/2026-09-22-theoretical-parser-result.md`.

---

## 0. Verified state

```bash
python -m pytest -q                          # 883 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
python3 -c "from app.eval.runner import _prompt_sha256; print(_prompt_sha256())"
                                             # aa7659f1929184ea — must not move
```

Branch `worktree-eval-harness`, PR #2. `SCHEMA_VERSION` = 1. Split frozen at
`6d174d5e4f1b9228`. Both H100s idle.

**You do not need to paste command output.** `./rescore_onepage.sh` writes every
digest into `docs/eval/`, which is in the repo and is the sanctioned view — say
"done" and the agent reads them.

---

## 1. PICKING THIS UP ON ANOTHER DEVICE

The repo is self-contained for code. **Five things are not in it and must be set
up by hand**, and three of them fail *silently* if you skip them.

### 1. Install the git hooks — FIRST, before any commit

```bash
git clone <remote> sindri && cd sindri
git checkout worktree-eval-harness
./install-hooks.sh
```

The client-data pre-commit guard used to live only in `.git/hooks`, which git
neither clones nor tracks — so a fresh checkout had **no guard at all and
nothing said so**. It is now versioned in `hooks/` and `install-hooks.sh` points
`core.hooksPath` at it, which also covers every worktree from one setting.

### 2. `~/.claude/sindri-protected-paths`

One absolute path per line — the client-data roots the hook refuses to stage.
Without it the name and content checks still run, but the **path** check cannot,
and that is the one that catches a symlink pointing into the corpus.
`install-hooks.sh` warns if it is missing.

### 3. `~/.claude/hooks/sindri-guard.py` and `test-sindri-guard.sh`

The agent's Bash guard — it refuses commands that would pull client content into
an AI context, and it has been right every time it fired. Copy both from the
previous machine, then **verify `32 passed, 0 failed`**. Not versioned here
because it is agent configuration rather than project code.

**Do this BEFORE the corpus arrives on the machine, not after.** A missing guard
fails silently in exactly the way this section warns about: commands naming the
protected root simply run, and nothing says so. On 2026-09-21 a session ran on a
machine with no guard; only the sanctioned CLI was used, so nothing improper
happened, but nothing would have stopped it either.

### 4. `~/.claude/sindri-doc-salt` — the one that breaks quietly

The doc-id salt. Every id in `docs/eval/*.json` is hashed with it. **A different
salt produces different hashes, so per-document analysis silently stops joining
to everything already published** — `worst_docs`, `per_doc_deltas`,
`dropped_tolerances.docs`. Copy the file across by hand.

**Never commit it.** It is the anonymisation key: with it and the client's part
numbers, the published digests become de-anonymisable.

### 5. The `tesseract` binary

A system package, not a pip dependency (`pytesseract` is only the wrapper).
Without it the suite reads **`6 failed`**: five in
`tests/eval/test_balloon_cv.py` and one in `tests/eval/test_ingest.py`, all
`TesseractNotFoundError`. That looks like broken code and is not. With it:
`883 passed, 2 skipped`.

### And the client corpus itself

`$HOME/sindri-client-data` — never in git, by design. `setup_client_data.py`
builds the layout; `sync_client_data.sh` moves drawings to the GPU host and
brings dumps back. Gold never leaves the operator's machine.

### The GPU host

ssh alias `4mehpc4_3` must exist in your ssh config. Deploy is push +
**detached** checkout — see `CLAUDE.md` §5; the obvious recipe works exactly once
and then blocks itself.

---

## 2. What is settled

**The input to the read holds the remaining quality, not the model reading it.**

| evidence | effect on `field_acc` |
|---|---|
| degrade the boxes (32B detector, same 72B reader) | **−0.206** |
| change the reader on fixed boxes (32B → 72B) | +0.013 |
| best fine-tune on fixed boxes (`read-lora-v1`) | +0.039 |
| **more context (`_CROP_PAD` 6 → 24)** | **+0.049** |

**Shipped: `_CROP_PAD = 24`** — dev production 133.93 → **131.87**, better under
6 of 6 weightings, `ci95` spans zero so robust-but-not-significant at 15
documents. Detection came back bit-identical.

**The crop lever is spent.** Three global doses plus one height-dependent dose
mapped the curve. `r3-tallpad` hit its target more precisely than any arm in the
campaign — `120-200` px 0.523 → 0.585, **every other band bit-identical** — and
still lost at 132.53, 0 of 6 weightings.

**Why it lost is the transferable lesson.** `flagged_error` and
`flagged_correct` both cost 1, so **fixing a read on a row the reviewer was
already going to check saves nothing**. That arm fixed 6 flagged rows (zero
saving) and broke 2 unflagged ones (+5 each). Only converting an ESCAPED error
pays — which is exactly what `read-lora-v1`'s win was made of. **Ask which
taxonomy bucket the fixed rows are in before proposing any read change.**

---

## 3. The open lead, and the next task

`read_accuracy_by_kind` on the shipped config:

| kind | n | share | `field_acc` | escaped |
|---|---|---|---|---|
| `dimension` | 189 | 84.8% | 0.6085 | 38 |
| `gdt` | 17 | 7.6% | 0.1765 | 11 |
| `theoretical` | 9 | 4.0% | **0.0000** | 8 |
| `surface` | 4 | 1.8% | **0.0000** | 4 |
| `note` | 4 | 1.8% | **0.0000** | 3 |

**34 non-dimension rows (15.2%) carry 26 of 64 silent errors (41%)**, and three
kinds never read correctly at all. `gold → Theoretical` totals **exactly 9** —
every `theoretical` prediction — because `parse_value` sets
`char_type = THEORETICAL` unconditionally and **gold's vocabulary has no such
value**. Those rows are wrong by construction. Full analysis:
`docs/plans/2026-09-17-read-accuracy-by-kind.md`.

### THE NEXT TASK, concretely

> **DONE 2026-09-22, and the arm LOST: bound exactly zero, reverted.** Kept
> below as the record of what was registered. See `docs/plans/2026-09-22-theoretical-parser-result.md`.

1. **Take the reparse baseline** (CPU seconds, no GPU). `score --reparse-check`
   with an unmodified parser must report `identical == n_pairs` — that is the
   gate proving the offline reconstruction is sound. The command is in §3 of the
   by-kind document.
2. **Make the candidate edit**: in `parser.parse_value`, stop forcing
   `char_type = THEORETICAL` for `hint == "theoretical"` and classify from the
   text instead (Ø → Diameter, R → Radius, else Distance). The box means *this
   dimension carries no tolerance*, not a different characteristic type, and
   `subtype` already records that it was boxed.
3. **Re-run the check.** `would_fix - would_break` is the bound. **Bounded above
   at 4 of the 9 rows** — 2 have gold `Flatness`, 3 have no mappable gold type —
   and a `char_type` fix only makes a row fully correct if nominal and both
   tolerances already agree.
4. Keep or revert as one commit. **Do not ship on the bound alone**; if it is
   positive, it still needs a scored run.

**This is not the closed char_type dead end.** §3 closes *synonym-map*
additions, about gold's vocabulary. This is the prediction side — and §3 itself
points here when it calls `parser.py` inferring Diameter from a leading Ø a
read-stage fault.

---

## 4. After that, in order

1. **Re-derive `weights.json` with the client.** Zero GPU, and now the deciding
   number three times over: whether `read-lora-v1` ever ships (needs a silent
   error to cost ≥9.4× a wasted re-check against today's 5×), whether
   `r3-tallpad`'s trade was worth taking, and how much a flagged-row fix is
   worth at all.
2. **Re-measure the shipped crop win on the test split.** A −2.07 that exists
   only on the tuned split is worth much less than one that survives where
   layouts are unfamiliar — and §2 predicts the effect should be LARGER there.
3. **`gdt`** — 17 rows at 0.1765, the largest non-dimension bucket, with a
   differently-shaped confusion (`gold → Flatness` totals 12, mostly from
   unmapped gold). The `theoretical` change was measured first (2026-09-22) and
   bounded at **zero** — its rows had a second fault under the labelling one.
   **Asked and answered for `gdt` the same day**: 8 of its 14 wrong rows are
   wrong in `char_type` ONLY, at least 5 escaped, worth at least −1.67. It is
   now the strongest GPU-free lead. Which side of the char_type is wrong decides
   the fix; see §5 of `2026-09-22-theoretical-parser-result.md`.
4. Unchanged from the 2026-09-14 decision doc: unblock route A serving,
   multi-page coverage (+8 drawings), tiled rendering (+19).

---

## 5. What the client should be told

* **Scope: 75 of 99 drawings** (75.8%) — 19 oversized, 8 multi-page, 3 both. The
  OVERSIZED exclusion is the larger by 2.4×; every earlier scope statement
  missed it.
* **Quality is a RANGE**: ~132 review cost on typical single-sheet drawings,
  ~165 on structurally atypical ones. Never the dev figure alone.
* **A corpus-representative estimate is worse than dev** even before template
  difficulty — dev sits 13 gold rows below the corpus unlocated rate, ~+8.7/doc.
* **Excluded is not "fails"** — oversized drawings still produce output at 0.371
  recall against 0.728.
