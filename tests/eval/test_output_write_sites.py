"""Every agent-reachable runner output is written through outputs.write_atomic.

Security review of H1 Milestone A, finding 3: a checker only helps if the
write that follows it cannot go around it. A plain `write_text` follows a
link or a hardlink planted at the target name AFTER the check, and leaves a
half file on a crash; `write_atomic` replaces the name instead. So the write
paths of the allowlisted subcommands are scanned statically for any direct
write. Milestone B wired them all; a new direct write fails this test."""
import ast
import inspect
import textwrap

import pytest

from app.eval import dump, review, runner, splits, verify

# (module, function) for every write path of predict, ingest, verify, score,
# split, variants, plus the class-A summary/compare digests and the two
# helpers those commands write through.
WRITE_SITES = [
    (runner, "_cmd_variants"), (runner, "_cmd_ingest"), (runner, "_cmd_split"),
    (runner, "_cmd_predict"), (runner, "_cmd_score"), (runner, "_cmd_compare"),
    (runner, "_cmd_summary"), (runner, "_cmd_review_tally"),
    (runner, "_cmd_reviewer_grade"), (verify, "cmd_verify"),
    (splits, "save_splits"), (dump, "save_dump"),
    (review, "write_deck"), (review, "write_tally"),
]

_WRITE_METHODS = {"write_text", "write_bytes"}
_COPIES = {"copy", "copy2", "copyfile", "copytree", "move"}


def _is_write_mode(node: ast.Call) -> bool:
    mode = None
    if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
        mode = node.args[1].value
    for kw in node.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            mode = kw.value.value
    if mode is None:
        # Path.open(mode) takes the mode FIRST.
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            mode = node.args[0].value
    return isinstance(mode, str) and any(c in mode for c in "wax+")


def direct_writes(source: str):
    """(line, what) for each write that bypasses write_atomic."""
    found = []
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        if name in _WRITE_METHODS:
            found.append((node.lineno, name))
        elif name == "open" and _is_write_mode(node):
            found.append((node.lineno, "open-for-write"))
        elif (isinstance(f, ast.Attribute) and name in _COPIES
              and getattr(f.value, "id", None) == "shutil"):
            found.append((node.lineno, f"shutil.{name}"))
        elif (isinstance(f, ast.Attribute) and name in ("replace", "rename")
              and getattr(f.value, "id", None) == "os"):
            # A hand-rolled atomic writer: the second writer this test exists
            # to keep out.
            found.append((node.lineno, f"os.{name}"))
    return found


def test_the_scanner_sees_each_kind_of_direct_write():
    """Guards the xfail below: a scanner that sees nothing would XPASS."""
    src = """
    def f(p, q):
        p.write_text("x")
        q.write_bytes(b"x")
        open(p, "w")
        open(p, mode="a")
        p.open("wb")
        shutil.copy(p, q)
        os.replace(p, q)
        open(p)                 # a read: not a write
        p.open()                # a read
        p.replace(q)            # a Path method: not os.replace (pull.py's tar)
        write_atomic(p, "x")    # the sanctioned writer
    """
    assert [w for _, w in direct_writes(src)] == [
        "write_text", "write_bytes", "open-for-write", "open-for-write",
        "open-for-write", "shutil.copy", "os.replace"]


def test_there_is_one_atomic_writer():
    """runner had its own `_write_atomic` for the grading report, which
    unlinked a link planted at the partial name instead of refusing it. Two
    writers drift; the reviewer-grade path goes through outputs.write_atomic
    like every other output."""
    assert not hasattr(runner, "_write_atomic")
    src = inspect.getsource(runner._cmd_reviewer_grade)
    assert direct_writes(src) == []
    assert "_write_checked(" in src
    helper = inspect.getsource(runner._write_checked)
    assert "write_atomic(" in helper and direct_writes(helper) == []


@pytest.mark.parametrize("module,name", WRITE_SITES)
def test_every_write_site_still_exists(module, name):
    """A rename would make its scan below vacuous."""
    assert callable(getattr(module, name))


@pytest.mark.parametrize("module,name", WRITE_SITES,
                         ids=[n for _, n in WRITE_SITES])
def test_write_paths_go_through_write_atomic(module, name):
    assert direct_writes(inspect.getsource(getattr(module, name))) == []
