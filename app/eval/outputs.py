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
import sys
from pathlib import Path
from typing import Iterable, Sequence

from app.eval.review import (ProtectedRootsError, _carries_marker,
                             _protected_roots, inside_protected)

# What a check may raise that must become a fixed refusal: a filesystem
# error, or a roots file that cannot be trusted (which fails CLOSED -- it is
# never read as "no roots").
_CHECK_ERRORS = (OSError, ProtectedRootsError)

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
        if a.resolve() == b.resolve():
            return True
        return a.exists() and b.exists() and os.path.samefile(a, b)
    except (OSError, RuntimeError):
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
    except (OSError, RuntimeError):
        # RuntimeError: a symlink loop on Python < 3.13.
        raise OutputRefused(f"the {what} path cannot be resolved")
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


def check_client_dir_out(path, *, subdir: str, legacy: Sequence[str],
                         avoid: Sequence = ()) -> Path:
    """Class C: a directory of client-value files, directly under
    <root>/<subdir>. Absent, empty, or ours: it holds our marker, OR it is a
    legacy run whose every entry matches `legacy` -- runs written before this
    check carry no marker, and predict resume on the GPU host depends on
    them. A link or subdirectory refuses it even when marked: nothing we
    write is one, and a link entry is how a write would reach gold."""
    target = _target(path, "output directory", avoid)
    try:
        if not _in_subdir(target.parent, subdir):
            raise OutputRefused("this output holds client data; it may only be "
                                "written directly under <protected root>/"
                                f"{subdir}/")
        if not target.exists():
            return target
        if not target.is_dir():
            raise OutputRefused("the output directory path is not a directory")
        entries = list(target.iterdir())
        for e in entries:
            if e.is_symlink() or not e.is_file():
                raise OutputRefused("the output directory holds a link or "
                                    "subdirectory; refusing to write into it")
        if not entries or _carries_marker(target / DIR_MARKER, DIR_KIND):
            return target
        if not all(any(fnmatch.fnmatchcase(e.name, g) for g in legacy)
                   for e in entries):
            raise OutputRefused("the output directory holds files that are "
                                "not this tool's output")
    except _CHECK_ERRORS:
        raise OutputRefused("the output directory cannot be checked") from None
    return target


def claim_dir(target: Path) -> None:
    """Create the directory and mark it ours, so a later run (or a resume)
    recognises it. Idempotent. Call it on a path a class-C check returned,
    BEFORE the first file is written: a crash that leaves a `.partial` in an
    unmarked legacy dir would otherwise make it fail the legacy rule."""
    try:
        target.mkdir(parents=True, exist_ok=True)
        marked = _carries_marker(target / DIR_MARKER, DIR_KIND)
    except OSError:
        raise OutputRefused("could not create the output directory")
    if not marked:
        write_atomic(target / DIR_MARKER, json.dumps({"kind": DIR_KIND}))


def write_atomic(path, data) -> None:
    """`<name>.partial` -> fsync -> os.replace. The rename REPLACES a symlink
    or a hardlink at the target name, so the other name of a hardlink (a gold
    file, an input) keeps its bytes. The parent must already exist: creating
    it is the caller's decision, made on the checked path."""
    p = Path(path)
    partial = p.with_name(p.name + ".partial")
    raw = data if isinstance(data, bytes) else data.encode("utf-8")
    created = False
    try:
        # A leftover partial from a crash, or a link planted at the
        # predictable name: remove it, then create exclusively and without
        # following, so the bytes can only land in a fresh file of our own.
        if partial.is_symlink() or partial.exists():
            partial.unlink()
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                     | getattr(os, "O_NOFOLLOW", 0), 0o644)
        created = True
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(partial, p)
    except OSError:
        if created:
            try:
                partial.unlink()
            except OSError:
                pass
        raise OutputRefused("could not write the output file")
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
    be written that its own next run may not replace."""
    body = {k: v for k, v in digest.items() if k != "kind"}
    text = json.dumps({"kind": kind, **body}, indent=1, ensure_ascii=False)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise OutputRefused("could not create the output directory")
    write_atomic(target, text)
    return text
