# Runner output hardening (H1) — design

Status: approved decisions 2026-10-23 (operator answered the three open
questions in §4). Implements handoff `2026-10-23-session-handoff.md` §3.2.

## 1. The hole

The Bash guard allowlists `python -m app.eval.runner <probe|headers|ingest|split|
predict|score|compare|summary|variants>`. It checks the *command shape*, not the
arguments, so any output flag of an allowlisted subcommand is an agent-chosen
write path. Most flags do `Path(args.x).write_text(...)` with no suffix, symlink,
overwrite or location check. An agent could aim `summary --out` at a gold file,
`predict --out` at an existing run directory, or `score --out` at the frozen
split file. C2 closed this for its own flags (`app/eval/review.py`
`check_report_path` / `check_digest_path`); the older flags stay open. The guard
must NOT be widened to fix this: the CLI has to refuse by itself.

## 2. Inventory and classification (every output flag)

Line numbers are `app/eval/runner.py` on 2026-10-23.

| Flag | Writes | Class | Agent-reachable |
|---|---|---|---|
| `summary --out` | run digest (counts, salted ids) | A | yes |
| `compare --out` | comparison digest | A | yes |
| `score --policy-out` | policy-check digest | A | yes |
| `score --gentol-out` | gentol-check digest | A | yes |
| `score --phantom-out` | phantom-profile digest | A | yes |
| `score --drop-out` | drop-check digest | A | yes |
| `review-tally --out` | counts-only tally | A | no (operator) |
| `score --out` | `RunReport` (client values) | B-file | yes |
| `split --out` | split file (part numbers; FROZEN) | B-file | yes |
| `variants --out` | list of part numbers | B-file | yes |
| `predict --out` | dir of `*.pred.json` (client values) | C | yes |
| `ingest --out` | dir of `*.gold.json` + `doc_id_map.json` | C | yes |
| `verify --out` | copy of a run dir with verdicts | C | no (GPU host) |
| `score --grade-out`, `--out` with `--reviewer-grade`, `--review-deck` | | already hardened (C2, deck) | — |

`verify --out` is not allowlisted but is the same shape as `predict`; it shares
the class-C checker at no extra cost.

Classes: **A** = counts-only digest meant for `docs/eval/` or the scratchpad.
**B-file** = a single client-value (or frozen) file that belongs in the protected
root. **C** = a directory of client-value files.

## 3. Rules

One new module, `app/eval/outputs.py`, three checkers and one writer. All refusal
messages are fixed sentences (no path, value or exception text: they print where an
agent reads and a path can be client text) and raise `OutputRefused`; the runner
turns it into exit 1. Every check runs before any scoring or prediction work.

**Common to all.** The target path itself must not be a symlink (checked BEFORE
`resolve()`, or the link is followed to gold). It must not equal any input of the
run (`summary` report, `compare` reports, `score`'s `--pdfs`-independent inputs such
as `--run` / `--splits` / `--manifest`). `OSError` is wrapped. Writes are atomic
(`<name>.partial` → `fsync` → `os.replace`), so an existing hardlink or symlink at
the target name is replaced, never written through.

**Class A (`check_digest_out`).**
suffix `.json`; resolved path **outside** every protected root (a digest inside
the root could never be read and could replace client data); if the target exists
it must carry the digest's `kind` marker as the first key, otherwise refused.
Every class-A digest now begins `{"kind": "<name>", ...}`:
`run-summary-digest`, `run-comparison-digest`, `policy-check-digest`,
`gentol-check-digest`, `phantom-profile-digest`, `drop-check-digest`,
`review-tally-digest`. Digests already in `docs/eval/` have no marker, so
regenerating one means deleting it first (deliberate; they are committed).

**Class B-file (`check_client_file_out`).**
If protected roots are configured the resolved path must be inside one AND in its
conventional subfolder: `reports/` for the score report (`*.report.json`), `meta/`
for the split (`*.json`) and variants (`*.txt`). Existing files: the score report
may be replaced only if it carries `"kind": "run-report"`; the split file and the
variants list are **never** overwritten (the split is frozen at `6d174d5e4f1b9228`).
`RunReport` gains `kind: str = "run-report"` as its FIRST field, so
`_carries_marker`'s opening-bytes peek works; old reports still load (default),
`SCHEMA_VERSION` stays 1, and an old report without the marker simply cannot be
overwritten.

**Class C (`check_client_dir_out`).**
Protected roots configured → the directory must be inside one and a **direct child**
of its conventional subfolder (`runs/<name>/` for predict and verify,
`gold/<name>/` for ingest). The directory must not exist, or be empty, or be
"ours": it holds a `.sindri-output` marker we write on first use, OR (legacy runs
written before this change, which must stay resumable, `predict` resume being load
bearing on the GPU host) it is non-empty and every entry is a regular file named
`*.pred.json` / `.complete` (predict, verify) or `*.gold.json` / `doc_id_map.json`
(ingest). Any symlink entry, subdirectory or other file refuses the directory.
Files inside are written atomically.

**No protected roots on the machine** (GPU container, CI, fresh clone): only the
"inside the root / conventional subfolder" rule is skipped (class A's "outside the
root" is trivially true). Symlink, suffix, marker, ours-or-empty and
never-overwrite rules still apply everywhere.

## 4. Decisions (operator, 2026-10-23)

1. Class A may overwrite only a file carrying its own kind marker (not "never",
   not "any path").
2. Class B/C must land in the fixed subfolders of the protected root (not
   "anywhere inside").
3. With no roots configured, skip only the root rule (not fail closed), so the GPU
   queue and tests keep working.

## 5. Out of scope

Widening the guard (forbidden); H2–H9 of the handoff; the C2 flags (done); reading
anything under the protected root. `docs/eval/` digests are not rewritten.

## 6. Consumers to keep working

`app/eval/experiment.py` and `app/eval/share_select.py` read digests: they must
tolerate the new leading `kind` key (tests pin this). The `share-pack` skill's
`summary --per-doc --out <scratchpad>/dev.json` still works: scratchpad is outside
the root, and the marker lets a re-run replace its own output.
`run_gpu_queue.sh`'s predict out dirs must pass the "ours or empty" rule; the
implementer checks every call site in the repo (`grep -rn "runner.*--out"`).

## 7. Tests (the acceptance bar)

Per flag, an agent-style probe proving each exploit is closed, run against a
temporary protected root via `SINDRI_PROTECTED_PATHS`: target is a symlink to a
fake gold file; parent directory is a symlink into the root (class A); `..`
traversal into the root; target is an existing non-marker file; wrong suffix;
output equals an input; class B/C outside its subfolder or in a grandchild; a
class-C dir holding a symlink entry, a foreign file, a subdirectory; a hardlinked
target (file content of the other link must be untouched); error text contains no
path. Plus: old report (no marker) loads and is refused as an overwrite target;
no-roots mode passes the legitimate cases; the full suite and the guard (32/0, not
widened) are re-run. A separate opus security reviewer re-checks after the
implementer, and again after fixes.

## 8. As built (2026-10-27) — where it differs from §3

Code: `app/eval/outputs.py` (checkers + the ONE writer, `write_atomic`);
probes: `tests/eval/test_runner_output_exploits.py` (no xfails).

* **Roots file.** Parsed like the guard: `#` comments, `~` expands. A line
  that is still relative, an unreadable/undecodable file, or an EXPLICIT
  `SINDRI_PROTECTED_PATHS` naming no regular file fails closed
  (`ProtectedRootsError`); only the default path may be absent. An empty
  file means "no roots" (tests use one). Skipping the root rule prints one
  stderr line per process.
* **Class A** also refuses a resolved path with a `.git` or `.claude`
  component, and a target inside an input directory. `review-tally` keeps
  the tally's existing first key, the deck kind `gdt-char-type-only`, as
  its marker (review-serve writes the same file).
* **Class B** files are a direct child of `<root>/reports/` or `meta/`,
  written 0600. The **review deck** and the **C2 grading report** follow
  the same rule under `reports/` (the deck: `.json`, never overwritten;
  both still refuse when no roots are configured).
* **Class C.** predict/verify may hold one real `_work/` subdirectory,
  scanned to any depth; any file with `st_nlink > 1`, link, special file or
  other subdirectory refuses the dir. The legacy-name rule judges top-level
  names only. Ingest may write `<root>/gold` itself (the documented layout)
  or a direct child; in `<root>/gold` itself, subdirectories carrying our
  `.sindri-output` marker are allowed. An ingest with no drawing/sheet pair
  refuses before writing anything.
* **score** refuses a `--*-out` digest flag whose check flag is absent.
* **runner.main** prints OutputRefused/ReviewRefused as
  `ERROR: output refused: <fixed sentence>` (a broken roots file shows its
  own sentence, never "cannot be checked"); a deliberate `RunnerError`
  (`app/eval/errors.py`, e.g. "mixed configs") as `ERROR: <message>`; any
  ValidationError, JSONDecodeError, UnicodeDecodeError or OSError from an
  input as `ERROR: could not read or parse an input file (<TypeName>)`; and
  ANY other exception as `ERROR: <subcommand> failed (<TypeName>)` -- never
  the exception text (pydantic quotes the input, PyMuPDF quotes the path,
  excel_gold quotes the sheet title).
* **Tally** (review-tally and review-serve's Finish): class-A rules incl.
  `.git`/`.claude`, the deck as an input. The **C2 grading digest** also
  refuses `.git`/`.claude`.

**Accepted limitations** (agent-only threat model: an agent chooses paths
on the command line and cannot race a running command):
1. Check-to-write race on parent directories: `write_atomic` re-resolves
   the approved parent before creating the partial and before the rename,
   which narrows but does not close it (that would need dir fds).
2. `_work/` is scanned at start-up only; the renderer writes into it with
   ordinary writes afterwards.
3. Re-ingesting a real corpus over a populated gold dir is allowed (no
   `--replace` flag); whether to keep it so is the operator's decision.
