"""Second lens over a campaign grade digest before it is committed (H7).

`grade_campaign` builds the digest from counts and closed words, and CLAUDE.md
§1 lets an agent read it because of that. A later edit that feeds a row value
into it, or a hand-edited file, would break the promise silently, and the
pre-commit hook only knows two field-name tokens. So every string in the
digest, keys included, must be a plain word: no whitespace, no non-ASCII
(client values are things like "Ø12.5 +0.1"), bounded length. Problems name
WHERE, never WHAT: quoting the offending string would copy it into a log.
"""
import re
from typing import Any, List

from app.eval.campaign_grade import DIGEST_KIND, EXACT_LEVERS, LEVERS

# Letters, digits and the separators the digest's own keys use
# ("fields:char_type+nominal", "recall_x_vs_y", salted hashes, run names).
_WORD = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:+/-]{0,95}\Z")
# The pre-commit hook blocks these as quoted tokens (CLAUDE.md §5); a digest
# key carrying one is a value stored under a field name.
_FORBIDDEN = frozenset({"upper_tol", "lower_tol"})


# The one place the digest carries prose: LEVERS and EXACT_LEVERS, closed
# tables the grader owns. Exact matches only, so a lever-shaped leak still fails.
_PROSE = frozenset(x for row in (*LEVERS, *EXACT_LEVERS.values())
                   for x in row[-2:] if isinstance(x, str))


def scan_grade_digest(digest: Any) -> List[str]:
    problems: List[str] = []
    if not isinstance(digest, dict) or digest.get("kind") != DIGEST_KIND:
        problems.append("kind marker missing or wrong")

    def bad(s) -> bool:
        if isinstance(s, str) and s in _PROSE:
            return False
        return not (isinstance(s, str) and _WORD.match(s)) or s in _FORBIDDEN

    def walk(node, where: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if bad(k):
                    problems.append(f"{where}: a key is not a plain word")
                    continue
                walk(v, f"{where}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{where}[{i}]")
        elif isinstance(node, str):
            if bad(node):
                problems.append(f"{where}: a string is not a plain word")

    walk(digest, "$")
    return problems
