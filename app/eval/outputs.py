"""Where an agent-chosen output path may write (design 2026-10-23, H1).

The Bash guard allowlists runner subcommands by SHAPE, not by argument, so
every output flag of an allowlisted subcommand is a write path an agent
picks. These checkers make each flag unable to replace anything but its own
previous output, and the writer makes the write itself unable to land
anywhere the checker did not approve (a link or hardlink at the target name
is replaced, never written through).

Messages are fixed sentences -- no path, no value, no exception text --
because they are printed where an agent reads, and a path can be client
text (a part number in a file name)."""
import fnmatch
import json
import os
import stat
import sys
from pathlib import Path
from typing import Iterable, Sequence

from app.eval.review import (ProtectedRootsError, _carries_marker,
                             _protected_roots, inside_protected)

# What a check may raise that must become a fixed refusal: a filesystem
# error, a path the OS rejects outright (ValueError: an embedded NUL byte),
# or a roots file that cannot be trusted (which fails CLOSED -- it is never
# read as "no roots").
_CHECK_ERRORS = (OSError, ValueError, ProtectedRootsError)

# Client values (class B/C) are readable by their owner only; a digest is
# counts-only and meant to be shared, so it keeps the usual mode.
CLIENT_FILE_MODE = 0o600
DIGEST_FILE_MODE = 0o644

# Path components a digest may never be written under, wherever they sit:
# what lands there is not a measurement but code or configuration that
# tools then act on (git hooks and config, Claude Code settings and hooks).
_FORBIDDEN_COMPONENTS = frozenset({".git", ".claude"})

# Announced once per process, so a machine that should have roots but has
# none (a missing or misnamed file) is visible rather than silently laxer.
_NO_ROOTS_ANNOUNCED = False
_NO_ROOTS_NOTICE = "no protected roots configured: root rule skipped"

# Written into a class-C directory on first use, so a later run (or a
# resume) recognises the directory as this tool's. Its CONTENT is checked,
# not just its name: the marker is what lets a directory hold any file.
DIR_MARKER = ".sindri-output"
DIR_KIND = "sindri-output-dir"


class OutputRefused(Exception):
    """Fixed-sentence refusal; the runner prints it and exits 1."""


def _same(a: Path, b) -> bool:
    """True when `b` names the same file as `a` -- by any spelling (`..`,
    a link in a parent) or as a hardlink, which no path comparison sees."""
    try:
        b = Path(b)
        rb = b.resolve()
        if a.resolve() == rb:
            return True
        # An input DIRECTORY (`--run`, `--gold`) is read, often globbed:
        # a new file inside it is as bad as replacing one of its files.
        if rb.is_dir() and a.resolve().is_relative_to(rb):
            return True
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except (OSError, RuntimeError, ValueError):
        return True      # cannot tell: refuse rather than guess "different"


def _target(path, what: str, avoid: Iterable) -> Path:
    """The rules common to every class: not a link, resolvable, not an
    input of the run. Returns the resolved path every later rule judges, so
    a rule can never be checked against one spelling and written to
    another."""
    p = Path(path)
    try:
        # BEFORE resolve(): resolving would follow the link -- to gold, a
        # dump, the frozen split. is_symlink() is also True for a dangling
        # link, whose target exists() would report absent.
        if p.is_symlink():
            raise OutputRefused(f"the {what} path is a symbolic link")
        target = p.resolve()
    except (OSError, RuntimeError, ValueError):
        # RuntimeError: a symlink loop on Python < 3.13. ValueError: a NUL
        # byte, which would otherwise escape as a traceback with the path.
        raise OutputRefused(f"the {what} path cannot be resolved") from None
    if any(a and _same(target, a) for a in avoid):
        raise OutputRefused(f"the {what} path is one of the run's inputs")
    return target


def check_digest_out(path, kind: str, avoid: Sequence = ()) -> Path:
    """Class A: a counts-only digest (docs/eval/ or the scratchpad).

    Outside every protected root -- inside one it could never be read, and an
    agent-picked path there could replace client data. Named `.json`. If the
    file exists it must be a previous digest of the SAME kind (marker as the
    first key), so a re-run replaces its own output and nothing else."""
    target = _target(path, "digest", avoid)
    if target.suffix != ".json" or target.name == ".json":
        raise OutputRefused("the digest file name must end in .json")
    # Judged on the RESOLVED path, so a link named anything else that points
    # into a repository's .git still counts.
    if _FORBIDDEN_COMPONENTS.intersection(target.parts):
        raise OutputRefused("a digest may not be written inside a .git or "
                            ".claude directory")
    try:
        if inside_protected(target):
            raise OutputRefused("a digest is the agent's output; it may not be "
                                "written inside a protected root")
        if target.exists() and not _carries_marker(target, kind):
            raise OutputRefused("a file that is not a previous digest of this "
                                "kind already exists there; move it away "
                                "deliberately")
    except _CHECK_ERRORS:
        raise OutputRefused("the digest path cannot be checked") from None
    return target


def _in_subdir(target_parent: Path, subdir: str) -> bool:
    """With roots configured the output must be a DIRECT child of
    <root>/<subdir> -- not anywhere inside the root, where gold/ and corpus/
    are inputs. `target_parent` is already resolved and the roots are too;
    `<root>/<subdir>` is deliberately NOT resolved, so a subfolder that is
    itself a link (pointing who knows where) fails closed. With no roots
    (GPU container, CI) the rule cannot be evaluated and is skipped; every
    other rule still applies (operator decision 3)."""
    global _NO_ROOTS_ANNOUNCED
    roots = _protected_roots()
    if not roots:
        if not _NO_ROOTS_ANNOUNCED:
            _NO_ROOTS_ANNOUNCED = True
            print(_NO_ROOTS_NOTICE, file=sys.stderr)
        return True
    return any(target_parent == r / subdir for r in roots)


def check_client_file_out(path, *, subdir: str, suffix: str, kind=None,
                          avoid: Sequence = ()) -> Path:
    """Class B-file: a file of client values, or a frozen one, directly under
    <root>/<subdir>. Replaced only if `kind` is given and the file carries it
    (the score report); the split and the variants list pass kind=None and
    are never overwritten -- the split is frozen."""
    target = _target(path, "output", avoid)
    if not target.name.endswith(suffix) or target.name == suffix:
        raise OutputRefused(f"the output file name must end in {suffix}")
    try:
        if not _in_subdir(target.parent, subdir):
            raise OutputRefused("this output holds client data; it may only be "
                                "written directly under <protected root>/"
                                f"{subdir}/")
        if target.exists() and not (kind and _carries_marker(target, kind)):
            raise OutputRefused("a file already exists there and is not a "
                                "previous output of this kind; move it away "
                                "deliberately")
    except _CHECK_ERRORS:
        raise OutputRefused("the output path cannot be checked") from None
    return target


_ENTRY_REFUSED = ("the output directory holds a link, a hardlinked file, a "
                  "special file or an unexpected subdirectory; refusing to "
                  "write into it")


def _plain_file(st) -> bool:
    """A regular file with ONE name. A second name (hardlink) is how a write
    could reach gold with no link to see, so it is refused outright rather
    than trusting every writer to replace atomically; a `cp -al` copy of a
    run is refused with it -- copy normally instead."""
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1


def _scan_work_dir(work: Path) -> None:
    """Everything under the work dir: real directories and single-named
    regular files only, at any depth. Not followed through links: a link
    anywhere is itself the refusal."""
    for dirpath, dirnames, filenames in os.walk(work, followlinks=False,
                                                onerror=_raise):
        for name in dirnames + filenames:
            st = os.lstat(os.path.join(dirpath, name))
            if not (stat.S_ISDIR(st.st_mode) or _plain_file(st)):
                raise OutputRefused(_ENTRY_REFUSED)


def _raise(err):
    raise err


def check_client_dir_out(path, *, subdir: str, legacy: Sequence[str],
                         avoid: Sequence = (), work_dir=None,
                         subdir_itself: bool = False) -> Path:
    """Class C: a directory of client-value files, directly under
    <root>/<subdir>. Absent, empty, or ours: it holds our marker, OR it is a
    legacy run whose every TOP-LEVEL entry matches `legacy` -- runs written
    before this check carry no marker, and predict resume on the GPU host
    depends on them. A link, a hardlinked or special file, or a
    subdirectory refuses it even when marked: nothing we write is one, and
    each is a way for a write to reach gold.

    `work_dir`: predict and verify render into `<out>/_work/<doc>/` and never
    remove it, so for them that one real subdirectory is allowed (and
    scanned to any depth by the same entry rules). `subdir_itself`: ingest's
    documented target is `<root>/gold/` itself, so it may write there as
    well as into a direct child."""
    target = _target(path, "output directory", avoid)
    try:
        # `<root>/<subdir>` itself (ingest's documented `--out <root>/gold`)
        # holds the dirs a `--out <root>/gold/<name>` ingest claimed, so a
        # subdirectory carrying OUR marker is allowed there -- only there,
        # and never an unmarked one. Nothing is written into it.
        is_subdir_itself = subdir_itself and _in_subdir(target, subdir)
        if not (_in_subdir(target.parent, subdir) or is_subdir_itself):
            raise OutputRefused("this output holds client data; it may only be "
                                "written directly under <protected root>/"
                                f"{subdir}/")
        if not os.path.lexists(target):
            return target
        if not target.is_dir():
            raise OutputRefused("the output directory path is not a directory")
        entries = list(target.iterdir())
        names = []
        for e in entries:
            st = os.lstat(e)
            if work_dir and e.name == work_dir and stat.S_ISDIR(st.st_mode):
                _scan_work_dir(e)
                continue
            if (is_subdir_itself and stat.S_ISDIR(st.st_mode)
                    and _carries_marker(e / DIR_MARKER, DIR_KIND)):
                continue
            if not _plain_file(st):
                raise OutputRefused(_ENTRY_REFUSED)
            names.append(e.name)
        if not names or _carries_marker(target / DIR_MARKER, DIR_KIND):
            return target
        if not all(any(fnmatch.fnmatchcase(n, g) for g in legacy)
                   for n in names):
            raise OutputRefused("the output directory holds files that are "
                                "not this tool's output")
    except _CHECK_ERRORS:
        raise OutputRefused("the output directory cannot be checked") from None
    return target


def claim_dir(target: Path) -> None:
    """Create the directory and mark it ours, so a later run (or a resume)
    recognises it. Idempotent. Call it on a path a class-C check returned,
    BEFORE the first file is written: a crash that leaves a `.partial` in an
    unmarked legacy dir would otherwise make it fail the legacy rule.
    `target` must be the checker's (resolved) return value: its parent is
    re-checked here, since the directory may have been swapped for a link
    since the check."""
    try:
        if _ancestor_moved(target):
            raise OutputRefused("the output location changed after it was "
                                "checked; refusing to write")
        target.mkdir(parents=True, exist_ok=True)
        marked = _carries_marker(target / DIR_MARKER, DIR_KIND)
    except (OSError, ValueError):
        raise OutputRefused("could not create the output directory") from None
    if not marked:
        write_atomic(target / DIR_MARKER, json.dumps({"kind": DIR_KIND}),
                     mode=CLIENT_FILE_MODE, expect_parent=target)


def _ancestor_moved(target: Path) -> bool:
    """`target` was resolved by a checker, so every ancestor was a real
    directory (or absent) then. True if the nearest one that exists now
    resolves somewhere else, i.e. it, or something above it, became a
    link since the check."""
    anc = target.parent
    # lexists, not exists: a DANGLING link swapped in must be judged too.
    while not os.path.lexists(anc) and anc != anc.parent:
        anc = anc.parent
    return anc.resolve() != anc


def _parent_moved(p: Path, expect_parent) -> bool:
    return expect_parent is not None and p.parent.resolve() != Path(expect_parent)


def write_atomic(path, data, mode: int = DIGEST_FILE_MODE,
                 expect_parent=None) -> None:
    """`<name>.partial` -> fsync -> os.replace. The rename REPLACES a symlink
    or a hardlink at the target name, so the other name of a hardlink (a gold
    file, an input) keeps its bytes. The parent must already exist: creating
    it is the caller's decision, made on the checked path.

    `expect_parent` is the resolved parent a checker approved. The parent is
    re-resolved before the partial is created and again immediately before
    the rename; if a directory on the way was swapped for a link in between,
    the write is refused (security review finding 2). This narrows the race
    to the instant between the last look and the rename; it does not close
    it, which would need openat-style directory handles."""
    p = Path(path)
    partial = p.with_name(p.name + ".partial")
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    created = False
    try:
        if _parent_moved(p, expect_parent):
            raise OutputRefused("the output location changed after it was "
                                "checked; refusing to write")
        # A regular leftover partial is ours, from a crash between create
        # and rename: remove it. Anything else at that predictable name (a
        # link, a directory, a fifo) we did not make -- refuse and leave it
        # for a human, never unlink or follow it.
        if partial.is_symlink() or (partial.exists() and not partial.is_file()):
            raise OutputRefused("something that is not a leftover partial "
                                "file sits at the temporary name; refusing "
                                "to write")
        if partial.exists():
            partial.unlink()
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0), mode)
        created = True
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        if _parent_moved(p, expect_parent):
            raise OutputRefused("the output location changed after it was "
                                "checked; refusing to write")
        os.replace(partial, p)
    except (OSError, ValueError, OutputRefused) as e:
        if created:
            try:
                partial.unlink()
            except OSError:
                pass
        if isinstance(e, OutputRefused):
            raise
        raise OutputRefused("could not write the output file") from None
    # The rename is durable only once the directory entry is; best effort,
    # since not every filesystem lets a directory be opened for fsync.
    try:
        dfd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def write_json_digest(target: Path, kind: str, digest: dict) -> str:
    """Serialise with `kind` FIRST -- the only thing `_carries_marker` peeks
    at -- create the parent, write atomically, return the text written. The
    `kind` argument wins over any `kind` the dict carries, or a digest could
    be written that its own next run may not replace. `target` must be
    `check_digest_out`'s (resolved) return value: the parent it approved is
    what the write insists on."""
    body = {k: v for k, v in digest.items() if k != "kind"}
    text = json.dumps({"kind": kind, **body}, indent=1, ensure_ascii=False)
    prepare_parent(target)
    write_atomic(target, text, mode=DIGEST_FILE_MODE, expect_parent=target.parent)
    return text


def prepare_parent(target: Path) -> None:
    """Create a checked file target's missing parent directories. `target`
    is a checker's (resolved) return value; an existing ancestor that now
    resolves elsewhere was swapped for a link after the check, so refuse
    before mkdir could create anything under the link's target."""
    try:
        if _ancestor_moved(target):
            raise OutputRefused("the output location changed after it was "
                                "checked; refusing to write")
        target.parent.mkdir(parents=True, exist_ok=True)
    except (OSError, ValueError):
        raise OutputRefused("could not create the output directory") from None
