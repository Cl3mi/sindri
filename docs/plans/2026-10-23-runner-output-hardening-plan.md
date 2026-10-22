# Runner output hardening (H1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** every output flag of `python -m app.eval.runner` that an agent can reach refuses to write anywhere it should not, without widening the Bash guard.

**Architecture:** one new module `app/eval/outputs.py` holds three checkers (class A digest, class B-file, class C dir) and an atomic writer; the runner calls them at argument-validation time and writes through the writer. `RunReport` and every class-A digest gain a leading `"kind"` marker so a re-run can replace its own output and nothing else. Design: `docs/plans/2026-10-23-runner-output-hardening-design.md` (read it first, §2 is the flag inventory, §3 the rules).

**Tech Stack:** Python 3, pytest, pydantic. No new dependency.

**Hard rules for every task (CLAUDE.md §1):** never read the protected client root; tests use only synthetic data under `tmp_path`; run pytest with ABSOLUTE worktree paths; single unpiped unchained Bash commands; never add to or widen `~/.claude/hooks/sindri-guard.py`; commit trailer per the operator's decision (see state file); do not commit `.claude/settings.json`, `test_docs/`, `sample.pdf`. Baseline: pytest 2359 passed / 2 skipped (2 `test_title_block` failures in a fresh worktree are the missing untracked fixtures, not bugs), node 298, guard 32/0.

## File structure

- Create `app/eval/outputs.py` — `OutputRefused`, `check_digest_out`, `check_client_file_out`, `check_client_dir_out`, `claim_dir`, `write_atomic`, `write_json_digest`. One responsibility: decide and perform an agent-chosen write.
- Modify `app/eval/models.py` — `RunReport.kind` first field.
- Modify `app/eval/runner.py` — wire all flags (sites listed in design §2; line numbers drift, grep `args.out`, `policy_out`, `gentol_out`, `phantom_out`, `drop_out`, `args.variants`).
- Modify `app/eval/review.py` — only if `_carries_marker` / `_protected_roots` need a public alias (prefer importing the existing names).
- Create `tests/eval/test_outputs.py` (unit), `tests/eval/test_runner_output_hardening.py` (one probe per flag, end to end through `runner.main`).
- Docs: `CLAUDE.md` §1/§2, `.claude/skills/share-pack/SKILL.md` (verify commands still valid), handoff, state file.

---

## Milestone A — the module and the marker (Tasks 1-3)

### Task 1: class A checker and the atomic writer

**Files:** Create `app/eval/outputs.py`; Test `tests/eval/test_outputs.py`

- [ ] **Step 1: failing tests.** Fixture `roots(tmp_path, monkeypatch)` makes `root = tmp_path/"client"` (with `reports meta runs gold` subdirs), writes `tmp_path/"roots.txt"` containing `str(root)`, and `monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(roots_file))`.

```python
import json, os
import pytest
from app.eval.outputs import (OutputRefused, check_digest_out, write_atomic)

KIND = "run-summary-digest"

def test_digest_inside_protected_root_refused(roots):
    with pytest.raises(OutputRefused):
        check_digest_out(roots / "reports" / "x.json", KIND)

def test_digest_outside_root_new_file_ok(roots, tmp_path):
    assert check_digest_out(tmp_path / "scratch" / "d.json", KIND) == (tmp_path / "scratch" / "d.json").resolve()

def test_digest_symlink_refused_even_to_a_safe_target(roots, tmp_path):
    real = tmp_path / "real.json"; real.write_text("{}")
    link = tmp_path / "link.json"; link.symlink_to(real)
    with pytest.raises(OutputRefused):
        check_digest_out(link, KIND)

def test_digest_parent_symlink_into_root_refused(roots, tmp_path):
    (tmp_path / "hop").symlink_to(roots / "reports")
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "hop" / "d.json", KIND)

def test_digest_dotdot_into_root_refused(roots, tmp_path):
    (tmp_path / "a").mkdir()
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "a" / ".." / "client" / "reports" / "d.json", KIND)

def test_digest_wrong_suffix_refused(roots, tmp_path):
    with pytest.raises(OutputRefused):
        check_digest_out(tmp_path / "d.txt", KIND)

def test_existing_foreign_file_refused_marked_file_replaced(roots, tmp_path):
    t = tmp_path / "d.json"; t.write_text('{"a": 1}')
    with pytest.raises(OutputRefused):
        check_digest_out(t, KIND)
    t.write_text(json.dumps({"kind": KIND, "n": 1}))
    assert check_digest_out(t, KIND)
    t.write_text(json.dumps({"kind": "other-kind"}))
    with pytest.raises(OutputRefused):
        check_digest_out(t, KIND)

def test_digest_equal_to_an_input_refused_even_when_marked(roots, tmp_path):
    t = tmp_path / "d.json"; t.write_text(json.dumps({"kind": KIND}))
    with pytest.raises(OutputRefused):
        check_digest_out(t, KIND, avoid=[t])

def test_input_aliased_by_hardlink_refused(roots, tmp_path):
    a = tmp_path / "in.json"; a.write_text(json.dumps({"kind": KIND}))
    b = tmp_path / "out.json"; os.link(a, b)
    with pytest.raises(OutputRefused):
        check_digest_out(b, KIND, avoid=[a])

def test_write_atomic_replaces_a_hardlink_instead_of_writing_through(tmp_path):
    gold = tmp_path / "gold.json"; gold.write_text("GOLD")
    t = tmp_path / "t.json"; os.link(gold, t)
    write_atomic(t, "new")
    assert gold.read_text() == "GOLD" and t.read_text() == "new"

def test_write_atomic_replaces_a_symlink_not_its_target(tmp_path):
    gold = tmp_path / "gold.json"; gold.write_text("GOLD")
    t = tmp_path / "t.json"; t.symlink_to(gold)
    write_atomic(t, "new")
    assert gold.read_text() == "GOLD" and not t.is_symlink()

def test_write_atomic_leaves_no_partial_and_message_has_no_path(tmp_path):
    t = tmp_path / "missing_dir" / "t.json"
    with pytest.raises(OutputRefused) as e:
        write_atomic(t, "x")          # parent absent: caller must create it
    assert str(tmp_path) not in str(e.value)
```

- [ ] **Step 2:** run `python -m pytest /ABS/PATH/tests/eval/test_outputs.py -q` → FAIL (module missing).
- [ ] **Step 3: implement `app/eval/outputs.py`.**

```python
"""Where an agent-chosen output path may write (design 2026-10-23, H1).

The Bash guard allowlists runner subcommands by SHAPE, so every output flag is
an agent-chosen write path. These checkers make each flag unable to replace
anything but its own previous output. Messages are fixed sentences: they are
printed where an agent reads, and a path can be client text."""
import fnmatch
import json
import os
from pathlib import Path
from typing import Iterable, Sequence

from app.eval.review import _carries_marker, _protected_roots, inside_protected

DIR_MARKER = ".sindri-output"
DIR_KIND = "sindri-output-dir"


class OutputRefused(Exception):
    """Fixed-sentence refusal; the runner prints it and exits 1."""


def _same(a: Path, b) -> bool:
    try:
        b = Path(b)
        if a.resolve() == b.resolve():
            return True
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except OSError:
        return True      # cannot tell: refuse


def _target(path, what: str, avoid: Iterable) -> Path:
    p = Path(path)
    if p.is_symlink():                  # BEFORE resolve(): it would be followed
        raise OutputRefused(f"the {what} path is a symbolic link")
    try:
        target = p.resolve()
    except (OSError, RuntimeError):
        raise OutputRefused(f"the {what} path cannot be resolved")
    if any(a and _same(target, a) for a in avoid):
        raise OutputRefused(f"the {what} path is one of the run's inputs")
    return target


def _in_subdir(target_parent: Path, subdir: str) -> bool:
    """With roots configured the output must be a direct child of
    <root>/<subdir>. With none (GPU container, CI) the rule cannot be
    evaluated and is skipped; every other rule still applies."""
    roots = _protected_roots()
    return not roots or any(target_parent == r / subdir for r in roots)


def check_digest_out(path, kind: str, avoid: Sequence = ()) -> Path:
    """Class A: a counts-only digest. Outside every protected root, `.json`,
    and it may replace only a previous digest of the same kind."""
    target = _target(path, "digest", avoid)
    if target.suffix != ".json" or target.name == ".json":
        raise OutputRefused("the digest file name must end in .json")
    if inside_protected(target):
        raise OutputRefused("a digest is the agent's output; it may not be "
                            "written inside a protected root")
    if target.exists() and not _carries_marker(target, kind):
        raise OutputRefused("a file that is not a previous digest of this "
                            "kind already exists there; move it away deliberately")
    return target


def check_client_file_out(path, *, subdir: str, suffix: str, kind=None,
                          avoid: Sequence = ()) -> Path:
    """Class B-file: client values or a frozen file. A direct child of
    <root>/<subdir>. Replaced only if `kind` is given and the file carries it
    (the score report); the split and variants files pass kind=None and are
    never overwritten."""
    target = _target(path, "output", avoid)
    if not target.name.endswith(suffix) or target.name == suffix:
        raise OutputRefused(f"the output file name must end in {suffix}")
    if not _in_subdir(target.parent, subdir):
        raise OutputRefused(f"this output holds client data; it may only be "
                            f"written directly under <protected root>/{subdir}/")
    if target.exists() and not (kind and _carries_marker(target, kind)):
        raise OutputRefused("a file already exists there and is not a "
                            "previous output of this kind; move it away deliberately")
    return target


def check_client_dir_out(path, *, subdir: str, legacy: Sequence[str],
                         avoid: Sequence = ()) -> Path:
    """Class C: a directory of client-value files. A direct child of
    <root>/<subdir>; absent, empty, or ours (marker, or a legacy run whose
    every entry matches `legacy`, so runs written before this check stay
    resumable). Any symlink, subdirectory or foreign file refuses it."""
    target = _target(path, "output directory", avoid)
    if not _in_subdir(target.parent, subdir):
        raise OutputRefused(f"this output holds client data; it may only be "
                            f"written directly under <protected root>/{subdir}/")
    if target.exists():
        if not target.is_dir():
            raise OutputRefused("the output directory path is not a directory")
        entries = list(target.iterdir())
        for e in entries:
            if e.is_symlink() or not e.is_file():
                raise OutputRefused("the output directory holds a link or "
                                    "subdirectory; refusing to write into it")
        names = [e.name for e in entries]
        if names and DIR_MARKER not in names and not all(
                any(fnmatch.fnmatchcase(n, g) for g in legacy) for n in names):
            raise OutputRefused("the output directory holds files that are "
                                "not this tool's output")
    return target


def write_atomic(path, data) -> None:
    """`<name>.partial` -> fsync -> os.replace. The rename REPLACES a symlink
    or hardlink at the target name; it is never written through."""
    p = Path(path)
    partial = p.with_name(p.name + ".partial")
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    try:
        if partial.is_symlink() or partial.exists():
            partial.unlink()
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(partial, p)
    except OSError:
        try:
            partial.unlink()
        except OSError:
            pass
        raise OutputRefused("could not write the output file")


def claim_dir(target: Path) -> None:
    """Create the directory and mark it ours, so a later run (or a resume)
    recognises it. Idempotent."""
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise OutputRefused("could not create the output directory")
    if not (target / DIR_MARKER).exists():
        write_atomic(target / DIR_MARKER, json.dumps({"kind": DIR_KIND}))


def write_json_digest(target: Path, kind: str, digest: dict) -> str:
    """Serialises with `kind` FIRST (what `_carries_marker` peeks for), creates
    the parent, writes atomically, returns the text written."""
    text = json.dumps({"kind": kind, **digest}, indent=1, ensure_ascii=False)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise OutputRefused("could not create the output directory")
    write_atomic(target, text)
    return text
```

- [ ] **Step 4:** the same pytest → PASS. Add unit tests for `check_client_file_out` and `check_client_dir_out` BEFORE moving on (Task 2 does this).
- [ ] **Step 5:** commit `eval: outputs.py — class A digest checker and atomic writer`.

### Task 2: class B-file and class C checkers

**Files:** Modify `tests/eval/test_outputs.py`

- [ ] **Step 1: failing tests** (roots fixture as above; `gold_like` = a file `root/gold/x.gold.json` containing `"GOLD"`):

```python
from app.eval.outputs import (check_client_file_out, check_client_dir_out, claim_dir, DIR_MARKER)

def test_file_out_must_be_direct_child_of_subdir(roots):
    ok = roots / "reports" / "a.report.json"
    assert check_client_file_out(ok, subdir="reports", suffix=".report.json")
    for bad in (roots / "gold" / "a.report.json",                 # wrong subfolder
                roots / "reports" / "sub" / "a.report.json",      # grandchild
                roots / "a.report.json"):                         # root itself
        with pytest.raises(OutputRefused):
            check_client_file_out(bad, subdir="reports", suffix=".report.json")

def test_file_out_outside_root_refused_when_roots_configured(roots, tmp_path):
    with pytest.raises(OutputRefused):
        check_client_file_out(tmp_path / "a.report.json", subdir="reports", suffix=".report.json")

def test_file_out_no_roots_configured_skips_only_the_root_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("SINDRI_PROTECTED_PATHS", str(tmp_path / "absent.txt"))
    assert check_client_file_out(tmp_path / "a.report.json", subdir="reports", suffix=".report.json")
    with pytest.raises(OutputRefused):                              # suffix still applies
        check_client_file_out(tmp_path / "a.txt", subdir="reports", suffix=".report.json")

def test_file_out_never_overwrites_without_kind(roots):
    t = roots / "meta" / "splits.json"; t.write_text("{}")
    with pytest.raises(OutputRefused):
        check_client_file_out(t, subdir="meta", suffix=".json")

def test_report_replaced_only_when_marked(roots):
    t = roots / "reports" / "r.report.json"
    t.write_text('{"schema_version": 1}')
    with pytest.raises(OutputRefused):
        check_client_file_out(t, subdir="reports", suffix=".report.json", kind="run-report")
    t.write_text('{"kind": "run-report", "schema_version": 1}')
    assert check_client_file_out(t, subdir="reports", suffix=".report.json", kind="run-report")

def test_file_out_symlink_to_gold_refused(roots):
    gold = roots / "gold" / "x.gold.json"; gold.write_text("GOLD")
    link = roots / "reports" / "r.report.json"; link.symlink_to(gold)
    with pytest.raises(OutputRefused):
        check_client_file_out(link, subdir="reports", suffix=".report.json", kind="run-report")

LEG = ("*.pred.json", ".complete")

def test_dir_out_new_empty_marked_legacy_ok(roots):
    new = roots / "runs" / "r6"
    assert check_client_dir_out(new, subdir="runs", legacy=LEG)
    new.mkdir(); assert check_client_dir_out(new, subdir="runs", legacy=LEG)       # empty
    (new / "a.pred.json").write_text("{}"); (new / ".complete").write_text("")
    assert check_client_dir_out(new, subdir="runs", legacy=LEG)                    # legacy, resumable
    claim_dir(new); (new / "other.txt").write_text("x")
    assert check_client_dir_out(new, subdir="runs", legacy=LEG)                    # marker => ours

def test_dir_out_refuses_foreign_dir_gold_dir_and_wrong_place(roots):
    gold = roots / "gold" / "g"; gold.mkdir(); (gold / "a.gold.json").write_text("GOLD")
    with pytest.raises(OutputRefused):
        check_client_dir_out(gold, subdir="runs", legacy=LEG)                      # wrong subfolder
    r = roots / "runs" / "r"; r.mkdir(); (r / "note.txt").write_text("x")
    with pytest.raises(OutputRefused):
        check_client_dir_out(r, subdir="runs", legacy=LEG)                         # foreign file, no marker
    with pytest.raises(OutputRefused):
        check_client_dir_out(roots / "runs" / "a" / "b", subdir="runs", legacy=LEG)  # grandchild

def test_dir_out_refuses_symlink_entry_subdir_and_symlink_dir(roots, tmp_path):
    r = roots / "runs" / "r"; r.mkdir()
    (r / "a.pred.json").symlink_to(roots / "gold" / "x.gold.json")
    with pytest.raises(OutputRefused):
        check_client_dir_out(r, subdir="runs", legacy=LEG)
    r2 = roots / "runs" / "r2"; r2.mkdir(); (r2 / "sub").mkdir()
    with pytest.raises(OutputRefused):
        check_client_dir_out(r2, subdir="runs", legacy=LEG)
    link = roots / "runs" / "r3"; link.symlink_to(roots / "gold")
    with pytest.raises(OutputRefused):
        check_client_dir_out(link, subdir="runs", legacy=LEG)
```

- [ ] **Step 2:** run → FAIL where checkers differ; **Step 3:** adjust `outputs.py` only if a test exposes a real gap (the code above is meant to pass); **Step 4:** PASS; **Step 5:** commit `eval: class B/C output checkers with the legacy-run rule`.

### Task 3: `RunReport.kind` marker

**Files:** Modify `app/eval/models.py` (`RunReport`, line ~290); Test `tests/eval/test_outputs.py`

- [ ] **Step 1: failing tests:** (a) `RunReport(...).model_dump_json()` starts with `{\n "kind": "run-report"` — build a minimal report using the existing test helper (grep `tests/eval` for a `RunReport(` factory and reuse it); (b) `RunReport.model_validate_json` on a JSON text WITHOUT `kind` still loads and yields `kind == "run-report"`; (c) a report written by `write_atomic` then passes `_carries_marker(p, "run-report")`.
- [ ] **Step 2:** FAIL. **Step 3:** add `kind: str = "run-report"` as the FIRST field of `RunReport`, with a comment: why first (opening-bytes peek) and why default (old reports stay readable; SCHEMA_VERSION stays 1). Check `_Versioned` base: `schema_version` may be declared there and therefore dump first; if so, verify `_carries_marker`'s regex (`\A\s*\{\s*"kind"`) fails and either declare ordering via `model_config`/field redefinition so `kind` dumps first, or serialise reports through `{"kind":..., **json.loads(...)}` in the writer. Pick the smaller change and test the first bytes.
- [ ] **Step 4:** PASS + `python -m pytest /ABS/tests/eval -q` (nothing else changed). **Step 5:** commit `eval: RunReport carries a kind marker, first key`.

**Checkpoint A:** write `docs/plans/2026-10-23-runner-output-hardening-plan.state.md` (milestone, commits, test counts, deviations).

---

## Milestone B — wire every flag (Tasks 4-6)

Common wiring pattern in `runner.py`:

```python
from app.eval.outputs import (OutputRefused, check_digest_out, check_client_file_out,
                              check_client_dir_out, claim_dir, write_atomic, write_json_digest)
```
`main()` catches `OutputRefused as e` → `print(str(e), file=sys.stderr); return 1` (mirror how `ReviewRefused` is handled near the `--reviewer-grade` code). Each command validates its output path(s) FIRST, before loading or scoring anything, and passes the run's inputs in `avoid`.

### Task 4: class A flags

**Files:** Modify `app/eval/runner.py` (`_cmd_summary`, `_cmd_compare`, the `--policy-out/--gentol-out/--phantom-out/--drop-out` blocks inside `_cmd_score`, `_cmd_review_tally`); Test `tests/eval/test_runner_output_hardening.py`

Kinds: `summary`→`run-summary-digest`, `compare`→`run-comparison-digest`, `--policy-out`→`policy-check-digest`, `--gentol-out`→`gentol-check-digest`, `--phantom-out`→`phantom-profile-digest`, `--drop-out`→`drop-check-digest`, `review-tally --out`→`review-tally-digest`. `avoid`: `summary` → `[args.report]`; `compare` → `[args.report_a, args.report_b]`; score blocks → `[args.run, args.out, args.splits, args.manifest]` where present; review-tally → `[args.deck]` (use `getattr(args, name, None)`; falsy entries are ignored).

- [ ] **Step 1: failing tests** — for EACH of the seven flags, an end-to-end test through `runner.main([...])` with synthetic inputs (reuse the fixtures other runner tests use: grep `tests/eval/test_runner*.py` for how a tiny run/report/deck is built; do not invent client-like data). Each flag gets: (1) output into `tmp_path/scratch/x.json` succeeds, first key is its `kind`; (2) re-run to the same path succeeds (marker); (3) pre-existing foreign `.json` is refused, file bytes unchanged; (4) symlink to a fake gold file is refused, gold unchanged; (5) path inside the fake protected root refused, nothing created there; (6) stderr contains none of: the target path, `Traceback`. Parametrise over the seven flags with a small table `{flag: (argv_builder, kind)}`.
- [ ] **Step 2:** FAIL. **Step 3:** replace each `Path(x).write_text(...)` with:

```python
target = check_digest_out(args.out, "run-summary-digest", avoid=[args.report])   # at the top of the command
...
write_json_digest(target, "run-summary-digest", digest)
```
Where the code currently prints `out` to stdout, print the text `write_json_digest` returns only if the existing behaviour printed the file's JSON (keep stdout byte-compatible except for the new leading `kind`; if that changes what tests pin, update those tests deliberately and say so in the commit). The `--policy-out`/... blocks currently write `blob` strings: change `blob` building to a dict where needed, or add a small `write_text_digest(target, kind, blob_json)` that inserts the kind key by `json.loads` → `write_json_digest`. For `score`, validate ALL four optional out flags at the top of `_cmd_score`, before scoring starts.
- [ ] **Step 4:** PASS, then `python -m pytest /ABS/tests/eval -q` and `python -m pytest /ABS/tests -q -k "experiment or share_select or summary or digest"` to prove consumers tolerate the leading `kind` (fix consumers if they iterate keys). **Step 5:** commit `eval: class A output flags refuse every path but their own digest`.

### Task 5: class B-file flags (`score --out`, `split --out`, `variants --out`)

**Files:** Modify `runner.py`; Test `tests/eval/test_runner_output_hardening.py`

- [ ] **Step 1: failing tests**, roots fixture active: `score --out`: inside `root/reports/n.report.json` succeeds and the file carries `"kind": "run-report"`; re-score to the same name succeeds; to an old marker-less report refused; `root/gold/x.report.json`, `root/reports/sub/x.report.json`, a symlink, a foreign file, output equal to `--run`'s input: all refused with the target's bytes unchanged. `split --out`: `root/meta/splits.json` succeeds once, second run refused (frozen), `root/gold/...` refused. `variants --out`: `root/meta/variants.txt` same. With roots NOT configured: the legitimate paths still succeed (GPU/CI mode), suffix and no-overwrite still enforced. No stderr line contains a path.
- [ ] **Step 2:** FAIL. **Step 3:**

```python
# _cmd_score, first thing (only when not --reviewer-grade, which has its own check)
out = check_client_file_out(args.out, subdir="reports", suffix=".report.json",
                            kind="run-report", avoid=[args.run, args.splits, args.manifest])
...
write_atomic(out, report.model_dump_json(indent=1))   # parent must exist: out.parent is the root's reports/ dir; with no roots configured do out.parent.mkdir(parents=True, exist_ok=True) first
# _cmd_split
out = check_client_file_out(args.out, subdir="meta", suffix=".json", avoid=[args.gold])
# save_splits writes with write_text: route its write through write_atomic, keep its mkdir
# _cmd_variants
out = check_client_file_out(args.out, subdir="meta", suffix=".txt", avoid=[args.pdfs])
write_atomic(out, "\n".join(...) + "\n")
```
`save_splits` is also used elsewhere; change it to `write_atomic` internally and keep its signature (check callers with grep first).
- [ ] **Step 4:** PASS + `python -m pytest /ABS/tests/eval -q`. **Step 5:** commit `eval: score/split/variants outputs stay in the protected root's own subfolders`.

### Task 6: class C flags (`predict`, `ingest`, `verify`)

**Files:** Modify `runner.py` (`_cmd_predict`, `_cmd_ingest`, `_cmd_verify`); grep `run_gpu_queue.sh`, `docs/`, `*.sh`, skills for every `--out` of these three commands and list them in the state file; Test `tests/eval/test_runner_output_hardening.py`

- [ ] **Step 1: failing tests.** `ingest`: `root/gold/g1` new succeeds, writes a `.sindri-output` marker, gold files and `doc_id_map.json`; second ingest into the same dir succeeds; into `root/runs/g1`, into a dir holding a foreign file, into a dir with a symlink entry (`a.gold.json` → another gold file: that file's bytes must stay unchanged after the refusal AND it must stay unchanged on the allowed paths because of atomic replace), into a symlinked dir: refused. `predict` (use the existing fake-backend fixture from `tests/eval/test_predict*.py`): `root/runs/r1` new/resume ok incl. a legacy dir (only `*.pred.json` + `.complete`, no marker) → resumes and skips done docs; foreign dir refused; no-roots mode ok. `verify`: same as predict; also its `--run` input dir is never its own `--out`.
- [ ] **Step 2:** FAIL. **Step 3:**

```python
out = check_client_dir_out(args.out, subdir="gold", legacy=("*.gold.json", "doc_id_map.json"),
                           avoid=[args.pdfs, args.excel, args.originals])
claim_dir(out)
... (out / f"{stem}.gold.json") written with write_atomic(...), doc_id_map.json likewise
# predict / verify: subdir="runs", legacy=("*.pred.json", ".complete"), avoid=[args.pdfs] (+ [args.run] for verify)
# every dump and `.complete` marker write inside the loop -> write_atomic
```
Find where `predict` writes dumps/`.complete` (grep `write_dump`, `.complete`, `dump_path`) and make those go through `write_atomic`; if a writer lives outside runner.py, change it there and keep its signature.
- [ ] **Step 4:** PASS, then the whole `tests/eval` dir. **Step 5:** commit `eval: predict/ingest/verify output dirs are claimed, checked, and written atomically`.

**Checkpoint B:** update the state file; run the full suite (`python -m pytest /ABS/tests -q`), node (298), guard (32/0).

---

## Milestone C — probes, review, docs (Tasks 7-9)

### Task 7: agent-style exploit probe suite

**Files:** Create `tests/eval/test_runner_output_exploits.py`

- [ ] One parametrised test per (flag × exploit) over the full flag inventory in design §2 (copy the table into the module docstring so a new flag cannot be forgotten): symlink at target; parent-dir symlink into the root; `..` traversal; hardlink of a gold stand-in as the target; existing foreign file; wrong suffix; output == input; wrong subfolder / grandchild (B, C); dir with symlink / subdir / foreign file (C); error text and stdout contain no path, no file content, no traceback. Assert the stand-in "gold" and "frozen split" bytes are identical after every attempt. Also a **completeness test**: introspect `runner._build_parser()` (or the equivalent) and fail if any option string ending `out` (or `--out`) of an allowlisted subcommand is missing from the probe table — this is what keeps H1 closed when a flag is added.
- [ ] Run: `python -m pytest /ABS/tests/eval/test_runner_output_exploits.py -q` → PASS (the probes are expected to pass at once: they verify Tasks 4-6; any failure is a real hole, fix it in `outputs.py`/`runner.py`). Commit `eval: exploit probes for every runner output flag`.

### Task 8: independent security review (opus, SEPARATE from the implementer)

Dispatch a fresh opus reviewer with: the design, the diff (`git diff main...HEAD`), and the instruction to (1) check spec compliance against design §3, then (2) try to break it: TOCTOU between check and write, `.partial` symlink pre-planting, case-insensitive suffix tricks, unicode/NUL in paths, directory entries named like the marker, `resolve()` on a nonexistent parent with a symlinked ancestor, a legacy dir containing a pred.json that is a hardlink to gold (atomic replace handles it; confirm), `--out` pointing at `/dev/stdout`-style special files, `avoid` entries that are directories. It must use absolute worktree paths for pytest, must not read the protected root, and its prompt must say "the protected root", never the path. Fixes go back to the implementer; a re-review follows. Record findings in the state file.

### Task 9: docs, skill, handoff

**Files:** `CLAUDE.md` §1 (output flags are checked; `kind` markers; delete an old digest before regenerating it; `KNOWN OPEN` in §2 → closed, point at this plan), `.claude/skills/share-pack/SKILL.md` (verify `summary --per-doc --out <scratchpad>/dev.json` still works; note a re-run to the same path is fine, a pre-existing foreign file is refused), `docs/plans/2026-10-23-session-handoff.md` §3.2 (mark H1 done), the state file, auto-memory `sindri-client-review-campaign.md`.

- [ ] Re-run the share-pack summary command example against a synthetic report in a test (add `tests/eval/test_share_pack_command.py` only if no test covers it), then final verification: `python -m pytest /ABS/tests -q` (expect 2359 + new, 2 skipped), `node --test "tests/js/*.test.mjs"` (298), `bash ~/.claude/hooks/test-sindri-guard.sh` (32/0, guard file untouched: `git diff --stat` must not list it, it lives outside the repo anyway). Commit docs. Merge to main only after the operator is told the results.

## Self-review (done at writing time)

Spec coverage: §2 inventory → Tasks 4-6 (+ completeness test in 7); §3 class A/B/C, no-roots, atomic, kind markers → Tasks 1-3; §6 consumers → Task 4 step 4; §7 tests → Tasks 1, 2, 7; review → Task 8; docs → Task 9. Types: `OutputRefused`, `check_digest_out(path, kind, avoid)`, `check_client_file_out(path, *, subdir, suffix, kind, avoid)`, `check_client_dir_out(path, *, subdir, legacy, avoid)`, `claim_dir(target)`, `write_atomic(path, data)`, `write_json_digest(target, kind, digest)` are used with these exact signatures throughout. Known judgement calls left to the implementer, each with a stated rule: how `_Versioned` orders `kind` first (Task 3), and exact fixtures to reuse (grep named per task).
