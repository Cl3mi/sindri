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
import json
import os
from pathlib import Path
from typing import Iterable, Sequence

from app.eval.review import _carries_marker, inside_protected


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
    except OSError:
        raise OutputRefused("the digest path cannot be checked")
    return target


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
