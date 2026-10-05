#!/usr/bin/env bash
# Wire this clone's git hooks to the versioned ones. Run ONCE per machine,
# before the first commit.
#
#   ./install-hooks.sh
#
# WHY THIS EXISTS. The client-data pre-commit guard was living only in
# .git/hooks, which git does not clone and does not track. So a fresh clone on a
# new machine had NO guard at all, and nothing said so -- the first commit that
# would have been blocked would simply have succeeded. That is the worst shape a
# safety check can take, and it is why the hook is now versioned in hooks/ and
# this script points core.hooksPath at it.
#
# core.hooksPath rather than copying into .git/hooks, for two reasons: it covers
# every worktree from one setting (this repo is developed in
# .claude/worktrees/eval-harness), and it keeps the hook in force identical to
# the hook under review instead of a copy that can drift.
#
# This is NOT the whole guard. Two things still live outside the repo and must
# be installed by hand on a new machine -- see "Picking this up on another
# device" in the current session handoff under docs/plans/:
#   * ~/.claude/sindri-protected-paths   the roots this hook refuses to stage
#   * ~/.claude/hooks/sindri-guard.py    the agent's Bash guard (32 tests)
#   * ~/.claude/sindri-doc-salt          the doc-id salt; WITHOUT THE SAME SALT
#                                        every hashed id in docs/eval/ stops
#                                        joining to the ids already published
set -euo pipefail

cd "$(dirname "$0")"
REPO_ROOT=$(git rev-parse --show-toplevel)
COMMON=$(git rev-parse --git-common-dir)
HOOKS="$REPO_ROOT/hooks"

[ -x "$HOOKS/pre-commit" ] || { echo "missing or non-executable $HOOKS/pre-commit" >&2; exit 1; }

git config core.hooksPath "$HOOKS"
echo "core.hooksPath -> $HOOKS"

# The hook refuses to stage anything resolving inside a protected root, and it
# reads that list from $HOME. An absent list is not an error -- the name and
# content checks still apply -- but it IS worth saying out loud, because the
# path check is the one that catches a symlink into the repo.
ROOTS="$HOME/.claude/sindri-protected-paths"
if [ -s "$ROOTS" ]; then
    echo "protected roots: $(wc -l < "$ROOTS") configured"
else
    echo "WARNING: $ROOTS is missing or empty." >&2
    echo "  Name and content checks still run, but the path check -- the one that" >&2
    echo "  catches a symlink pointing into the client corpus -- cannot." >&2
    echo "  Add one absolute path per line before working with real data." >&2
fi

echo "git common dir: $COMMON (hooks now apply to every worktree)"
