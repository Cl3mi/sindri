"""The client-data guard has to survive a clone.

It lived only in `.git/hooks`, which git neither clones nor tracks, so a fresh
checkout on a new machine had NO pre-commit guard and nothing said so: the first
commit that should have been blocked would simply have succeeded. A safety check
that disappears silently when the repo moves is the worst shape one can take.

So the hook is versioned in `hooks/` and `install-hooks.sh` points
`core.hooksPath` at it. These tests pin the properties whose failure would be
silent -- the same reason `tests/test_gpu_queue.py` exists for the GPU driver.
"""
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
HOOK = ROOT / "hooks" / "pre-commit"
INSTALLER = ROOT / "install-hooks.sh"


def test_the_hook_is_versioned_and_executable():
    """A non-executable hook is not run by git, and git would say nothing."""
    assert HOOK.is_file()
    assert HOOK.stat().st_mode & stat.S_IXUSR, "hook must be executable"


def test_both_scripts_are_syntactically_valid():
    for script in (HOOK, INSTALLER):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script


def test_the_hook_still_blocks_all_three_classes():
    """It guards three different leaks and each has its own failure mode:
    a client document added by name, a path resolving inside a protected root
    (a symlink into the repo), and a .json whose CONTENT carries extracted
    values. Losing any one of them is invisible until it matters."""
    text = HOOK.read_text(encoding="utf-8")
    assert "gold.json" in text and "pred.json" in text, "name check"
    assert "sindri-protected-paths" in text, "protected-root path check"
    assert "upper_tol" in text or "lower_tol" in text, "content check"


def test_the_escape_hatch_is_still_explicit():
    """SINDRI_ALLOW_DATA_COMMIT exists for a reviewed exception, and CLAUDE.md
    §5 says not to reach for it casually. It must stay an opt-in env var rather
    than becoming a default."""
    text = HOOK.read_text(encoding="utf-8")
    assert "SINDRI_ALLOW_DATA_COMMIT" in text
    assert ':-0}" = "1"' in text, "the hatch must default to OFF"


def test_the_installer_uses_hooksPath_not_a_copy():
    """core.hooksPath covers every worktree from one setting -- this repo is
    developed inside .claude/worktrees/ -- and keeps the hook in force identical
    to the hook under review, where a copy into .git/hooks can drift."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "core.hooksPath" in text
    assert "cp " not in text, "installing by copy lets the two versions drift"


def test_the_installer_warns_when_the_protected_roots_are_missing():
    """An absent roots file is not an error -- name and content checks still
    run -- but it disables the path check, which is the one that catches a
    symlink into the client corpus. Silence there would be the same defect as
    the missing hook."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "WARNING" in text and "sindri-protected-paths" in text
