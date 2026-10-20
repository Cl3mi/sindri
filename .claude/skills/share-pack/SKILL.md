---
name: share-pack
description: Use when the operator asks to export, pick or prepare a few drawing/Excel pairs to show the client (e.g. "export 3 good and 2 problematic drawings", "give me examples for the client"). Selects from counts-only digests and hands back ONE command for the operator to run.
---

# share-pack: pick drawings for the client, export in one command

**You never open the drawings or the exported files, and you cannot run the
export** -- it reads client PDFs, which the guard blocks for agents. Your job is
selection (counts and salted ids only) and handing over one ready command.

## Steps

1. **Digests** -- one per split you want drawings from, each as its OWN Bash
   call, output into the scratchpad. Use the shipped-policy controls unless the
   operator names other runs (`r4-control-scoped` = dev, `r4-controltest-scoped`
   = test; `docs/eval/*-summary.json` lists newer ones). Never train.
   ```
   python -m app.eval.runner summary <root>/reports/<run>-scoped.report.json --per-doc --out <scratch>/dev.json
   python -m app.eval.runner summary <root>/reports/<run2>-scoped.report.json --per-doc --out <scratch>/test.json
   ```
   `<root>` is `~/sindri-client-data`. The summary prints its whole digest; that
   is fine, it holds hashed ids and counts only. Never pass `--show-ids`.
2. **Select** -- map each digest to its predict RUN DIRECTORY (report name minus
   `-scoped`: `r4-control`, `r4-controltest`):
   ```
   python -m app.eval.share_select --digest r4-control=<scratch>/dev.json --digest r4-controltest=<scratch>/test.json --great 3 --problem 2 --out <scratch>/manifest.json
   ```
   Defaults and their rationale are in `app/eval/share_select.py`'s docstring
   (substantial drawings, no unflagged wrong values -- the client ranks
   precision over recall -- "problem" = visibly imperfect, never broken). Map the
   operator's wording onto its flags: "bigger drawings" -> `--min-gold`,
   "only really good ones" -> `--great-recall`, "more problems" -> `--problem`.
   If it warns it is short, loosen a flag rather than relaxing the escaped
   ceiling. The manifest holds hashes only. For criteria the flags cannot say,
   read `per_doc` from the digest yourself and write the same manifest shape:
   `{"runs": [{"run": "<run dir>", "hashes": ["..."]}]}`.
3. **Hand over one command**, in a code block, for the operator's own terminal.
   The scratchpad path is session-local, so write the manifest straight to a
   stable path outside the repo instead (`--out ~/client-share-manifest.json`
   in step 2) and use that here:
   ```
   cd <this worktree> && python export_share.py --manifest <manifest path> --out ~/client-share
   ```
   Say what each pick is (role, hashed id, one-line why from the selector) and
   that terminal output is hashes only, so they can paste it back for a check.
4. **Before it goes to the client**, tell the operator to open the "problem"
   pairs: the extra balloons may be dimensions the client chose not to balloon,
   which counts cannot show.

## Do not

- read, copy or `ls` anything under the protected root; no `cp`, no `--show-ids`;
- put real part numbers, run output with real ids, or the manifest in git;
- write the export output folder inside the repo or the protected root;
- present the client's own stamped drawings / Excel sheets as "ours": the export
  renders OUR prediction (confirmed rows only) onto the clean drawing.
