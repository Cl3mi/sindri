# Session handoff — 2026-10-07: the precision direction is CLOSED; next is human-in-the-loop

Written 2026-10-07 for a FRESH session. **Read `CLAUDE.md` first, then this.**
It supersedes `2026-09-26-session-handoff.md` as the state of play;
`2026-09-17-session-handoff.md` §1 is still the device-setup reference.

**One line:** the automated-quality direction (policy rules, read adapter,
general tolerance, phantom drops, verifier, parser fix) is finished and fully
written up. The product now ships few but mostly-true values unchecked, and
hands everything else to a reviewer. The next direction is the reviewer
themselves: grading the system's recommendations against what the human
does, cognitive forcing, and other HITL functionality.

---

## 0. Verified state

```bash
python -m pytest -q                          # 1293 passed, 2 skipped
bash ~/.claude/hooks/test-sindri-guard.sh    # 32 passed, 0 failed
```

Branch `worktree-eval-harness`, merged to `main` as it went (PR #2 merged
2026-10-05; later work pushed straight to `main`). Working tree clean.

## 1. Current quality — MEASURED (native predict runs on current code)

Controls for every later arm: **`r5-control` (dev) and `r5-controltest`
(test)**. Scoped: single-sheet drawings at full resolution. Dev has 15
documents and 311 gold values; test has 11 and 292. Weights miss 10 / escaped 5
/ false 2 / flag 1.

| | dev | test |
|---|---|---|
| **delivered precision** (true share of what ships unchecked) | **0.526** | **0.958** |
| shipped unchecked: correct / wrong / phantom | 41 / 7 / 30 | 23 / 1 / 0 |
| matched-only auto-accept precision | 0.854 | 0.958 |
| auto-accept rate (correct unchecked / gold values) | 0.132 | 0.079 |
| recall (gold values matched by a balloon) | 0.666 | 0.568 |
| field accuracy on matched rows (correct + flagged_correct) / matched | 0.440 | 0.277 |
| flagged for review (wrong / correct) | 109 / 50 | 119 / 23 |
| missed gold values (contended / isolated / unlocated) | 104 (33 / 60 / 11) | 126 (49 / 46 / 31) |
| false detections in total (most of them flagged) | 267 | 115 |
| mean review cost | 117.87 | 148.82 |
| **suggestion tray** (stage-2 drops; never exported unconfirmed) | 89 = 29 correct / 10 wrong / 50 phantom | 73 = 29 / 23 / 21 |

How the headline moved over this direction (dev):

| stage | matched precision | delivered precision | cost |
|---|---|---|---|
| shipped crop pad (r3-cropctx), before the policy | 0.533 | — | 131.87 |
| + flag/drop policy (r4-control) | 0.805 | 0.419 | 118.73 |
| + phantom drops (r5-control) | 0.854 | **0.526** | 117.87 |

**Reading it.** On test, almost everything shipped unchecked is true (23 of
24). On dev, 30 of 78 unchecked values are phantoms read at ≥ 0.99 confidence
that no gold-free rule can reach (§3). They are most likely real dimensions the
client chose not to balloon. Everything uncertain goes to a reviewer, either as
a flagged balloon or as a suggestion in the tray. Recall is the price of the
precision-first policy, accepted by the operator.

Delivered precision is the metric the client cares about (operator,
2026-10-05: every delivered value true, precision over recall at any recall).
Review cost cannot see phantoms (a false detection costs 2 flagged or not), so
quote delivered precision, not cost, as the headline.

## 2. What this direction shipped (each registered before pricing)

| change | evidence |
|---|---|
| **Flag/drop policy** (`nondim_kind`, `no_tolerance` flags; `contained_duplicate` drop) | `2026-09-25-policy-arms-result.md`; measured r4-control / r4-controltest |
| **Delivered precision** metric (`auto_accept.delivered_precision`, `false_unflagged`) | commit `273bb24`; CLAUDE.md §2 |
| **Phantom drops**, drop stage 2 (`conf_below_099 + material_kind + tight_cluster`) | `2026-10-06-phantom-drops-registration.md` / `-result.md`; measured natively by r5-control / r5-controltest, identical on all 37 digest aggregates |
| **Drop STAGES** (`ACTIVE_DROP_STAGES`, each judged on what the previous left; flags before drops) | `c83945c`; tests pin the unpriced single-pass outcome |
| **Suggestion tray**: stage-2+ drops shown violet in the reviewer UI, confirm/dismiss, never exported unconfirmed | `2026-10-07-suggestion-tray-design.md` / `-plan.md`; browser-checked; r5 re-score identical on all 40 keys |
| **Parser fix**: positive second tolerance ("20 +0,2 +0,1") | `2026-10-07-parser-plus-lower-registration.md` (kept; no current row has the shape) |
| Measurement tools: `--phantom-profile`, `--drop-check` (families `phantom`, `verifier`), `--gentol-check`, `runner verify` | each with tests and a counts-only digest |

## 3. What this direction CLOSED (do not retry — CLAUDE.md §3 has the numbers)

* **Deploying `read-lora-v1` by any route.** Harmful under the policy (+3.33 /
  +4.27): it fills missing tolerances with wrong ones, so `no_tolerance` stops
  flagging them. `2026-10-05-read-adapter-repricing-result.md`.
* **Filling the ISO 2768 general tolerance.** Train gate 12 fix / 63 break; most
  untoleranced reads have a PRINTED tolerance in gold.
  `2026-10-05-general-tolerance-result.md`.
* **A yes/no VLM verifier for phantoms.** It gives P(yes) ≥ 0.9 to 14 of 16
  train phantoms; no threshold passes. `2026-10-07-verifier-result.md`.

## 4. Parked (operator decision 2026-10-07: set aside, not closed)

1. **The client's real weights.** Today's weights rank finding values above
   keeping them true, the opposite of the client's priority.
2. **The remaining high-confidence phantoms.** Either ask the client what they
   leave unballooned (each answer becomes a priced rule), or learn their
   ballooning choice from train gold (a classifier: train → dev → test).
3. **Retraining the read adapter.** Only after `render_target` stops teaching
   the model to invent tolerances; little precision left on the read side
   (dev ships 7 wrong values unchecked, test 1).
4. **Table rows appear in detection order** until the first edit re-sorts
   them (pre-existing UI behaviour, not a precision issue).

## 5. Next direction — human-in-the-loop (starting brief, not a design)

The operator wants, in a fresh session:

* **Grading the system's recommendations against what the reviewer does**, to
  learn how often the system misjudges a value (flag decision wrong), misses a
  value, writes the wrong value, or fails some other common way. Brainstorm it
  properly; facts the design should start from:
  * The UI already produces every signal needed. Each row has a stable `id`, the
    proposed values, `needs_review` / `review_reasons`, `confidence`,
    `suggested`. The reviewer's actions are explicit operations in
    `app/static/js/state.js`: `opEditCell` (value corrected),
    `opBulkReview` / `opConfirmSuggestions` (accepted / confirmed),
    `opDeleteRow` (phantom removed), `opAddRow` (a missed value added by hand,
    `source: "manual"` via `/api/read_region`), and `opMoveRow`. Nothing is
    logged today: the session lives in the browser and is gone on export.
  * The eval taxonomy maps onto reviewer outcomes almost one to one:
    correct ↔ accepted unchanged; escaped/flagged error ↔ edited; false
    detection ↔ deleted; missed ↔ added by hand; flagged_correct ↔ a flag the
    reviewer did not need. A reviewer-action log graded the same way would let
    production sessions be scored WITHOUT client gold, and be compared with the
    gold-based numbers in §1.
  * Unflagged rows the reviewer never touched are the hard case: "accepted" and
    "not looked at" look identical unless the UI records what was viewed.
* **Cognitive forcing** for the rows most likely to be wrong, for example
  making the reviewer type or confirm a value before accepting it, so a flag is
  not waved through. The tray's rule that `Y` confirms suggestions only inside
  their own filter is a first, small instance.
* Privacy: production sessions contain client values. A log of reviewer
  actions is client data and must follow the same rules as the corpus
  (CLAUDE.md §1): counts-only digests may be committed, values never.

## 6. Operational notes this direction paid for

* **Pulls are the operator's**, and need the ABSOLUTE remote root:
  `./sync_client_data.sh pull 4mehpc4_3 /home/rebe_test3/sindri-eval-data <run> ~/sindri-client-data`.
  A bare `~` expands locally and pulls nothing.
* **The guard matches command TEXT.** A heredoc containing `.pdf`,
  `.pred.json` or `.gold.json`, even in synthetic test code, is refused; write
  such files with the file tools. Subagent prompts may not contain the
  protected path, so protected commands must run in the main session.
* **GPU host:** check no queue is running and which card is free, push to
  `from-operator`, `git checkout --detach from-operator`, rebuild
  `sindri-gpu-nf4`, run in tmux via `run_gpu_queue.sh`. Verify stages
  (`verifytrain/dev/test`) run `runner verify` on existing dumps instead of
  predicting.
* **UI checks:** the repo has no browser driver. Node tests for UI state rules
  live in `tests/js/` (run inside pytest, skipped without node). A real-browser
  drive used a throwaway Playwright venv in the session scratchpad plus a stub
  backend launcher; recreate it rather than adding Playwright to the repo.
