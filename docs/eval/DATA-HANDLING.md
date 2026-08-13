# Client data handling

The ~100 client drawings and inspection sheets (and everything derived from
them) are confidential. Two hard rules:

1. **No AI model reads them.** Not Claude Code, not a subagent, not a shell
   command an agent issues. The *offline* Qwen VLM inside the container is the
   one exception — that is the product doing its job on the GPU box.
2. **They never enter git.** Not the originals, not gold records, not
   prediction dumps, not un-redacted reports.

Everything below is enforced mechanically and verified, not by convention.

## Where the data lives

Outside the repository, under one protected root:

```
<protected-root>/            # e.g. ~/sindri-client-data — NOT in any git tree
  Orginalzeichnungen/        # clean drawings  -> pipeline input (predict)
  Gestempelte Zeichnungen/   # ballooned       -> gold balloon positions
  Berichte/                  # Excel sheets    -> gold values
  gold/                      # <doc>.gold.json + doc_id_map.json
  runs/<name>/               # <doc>.pred.json
  reports/                   # <name>.report.json
```

Because the root sits outside every git working tree, a git leak is
structurally impossible rather than merely blocked.

> **Note on the delivery:** clean *and* ballooned copies of each drawing were
> supplied. That is better than the plan assumed — `predict` runs on the clean
> `Orginalzeichnungen`, exactly what production sees, while gold balloon
> positions come from `Gestempelte Zeichnungen`. No balloon-stripping needed.

## Registering the root (the one manual step)

Add the absolute path to `~/.claude/sindri-protected-paths`, one per line:

```
/home/clemi/sindri-client-data
```

That file is read by the guard on every tool call. Nothing else needs changing.

## Layer 1 — the agent guard (PreToolUse hook)

`~/.claude/hooks/sindri-guard.py`, registered in `~/.claude/settings.json`, so
it applies to **every session, project, worktree and subagent** on this machine.

It denies:

| Vector | Example |
|---|---|
| Direct read | `cat <root>/x.pdf`, `Read(<root>/a.xlsx)` |
| Scripted read | `python3 -c "print(open('<root>/a.xlsx','rb').read())"` |
| Copy into repo | `cp -r <root>/pdfs .` |
| Listing / searching | `Glob`, `Grep`, `ls <root>` |
| Indirection | `bash leak.sh` where the *script* reaches into the root |
| Extraction tools | `pdftotext`, `libreoffice`, `tesseract`, … anywhere |
| File types anywhere | `*.pdf`, `*.xlsx/xls/xlsm/xlsb/ods/csv`, `*.gold.json`, `*.pred.json`, `*.report.json`, `doc_id_map*` |
| Handing the path to a subagent | `Agent(prompt="inspect <root>")` |

It fails **closed**: if the guard itself errors on a payload that mentions
protected data, the call is denied.

The single sanctioned exception is the reviewed CLI, whose stdout is
aggregate-only by construction:

```
python -m app.eval.runner <probe|headers|ingest|split|predict|score|compare|summary> ...
```

Chained or piped variants (`… && cat`, `… | head`) are **not** sanctioned —
that would smuggle a second command past the check.

Why a hook rather than `permissions.deny`: this machine allows
`Bash(python3 *)`. Permission rules match command *prefixes*, so an allowed
`python3 -c …` could read anything. Only a hook sees the whole command.

Re-verify at any time:

```bash
bash <scratch>/test-guard.sh     # 29 cases: attack vectors + normal work
```

## Layer 2 — git

* `.git/hooks/pre-commit` (via `core.hooksPath`, so **all** worktrees) rejects:
  newly added drawing/spreadsheet/gold/dump/report files, any staged path
  resolving inside a protected root, and any staged JSON whose *content*
  carries extracted values (`field_errors`, `raw_text`, `characteristics`,
  `position_pt`, `upper_tol`, `lower_tol`).
* `.gitignore` blocks the same patterns as a second line of defence.
* Deliberate, reviewed exception: `SINDRI_ALLOW_DATA_COMMIT=1 git commit …`

Already-tracked files (`tests/fixtures/sample.pdf`) still work — this guards
new leaks, not existing history.

## Layer 3 — anonymized, value-free output

Part numbers are short and guessable, so hashes are **salted**; the salt lives
at `~/.claude/sindri-doc-salt` (mode 0600, outside the repo).

* Every human-readable CLI line shows `a1b2c3d4` instead of `T1025300_B`.
  `--show-ids` opts back in — **for a human terminal only, never for an agent.**
* `ingest` writes `gold/doc_id_map.json` (hash → real id) so a human can trace
  a finding back to a drawing. Gitignored and blocked by the pre-commit hook.
* A `RunReport` embeds client values: `doc_scores[].pairs[].field_errors`
  spells out gold vs predicted (`"nominal: '6,5'!='5,5'"`). **Never read a
  report directly.** Use:

  ```bash
  python -m app.eval.runner summary <report.json>
  ```

  which emits aggregate metrics, taxonomy, config fingerprint and hashed
  worst-doc ids — safe to show an agent, commit, or paste into a ticket.

## What an agent may see

Allowed: file counts, join rates, balloon-count statistics, Excel column
captions, taxonomy histograms, review-cost/recall/escaped-rate metrics,
bootstrap CIs, hashed doc ids.

Never: drawing images, cell values, nominals/tolerances, raw VLM
transcriptions, real part numbers.

## Honest limits

* The guard stops *accidental* ingestion by a cooperating agent; it is not an
  adversarial sandbox. Anyone with shell access as this user can read the files.
* Protection keys off the registered root and the listed file types. Data
  copied elsewhere under a different extension is not covered — keep it under
  the root.
* `--show-ids` and `SINDRI_ALLOW_DATA_COMMIT=1` are deliberate human escape
  hatches. Do not use them in an agent session.
* The offline container VLM does read the drawings. That is intended, stays on
  the GPU box, and no drawing content is sent anywhere.
