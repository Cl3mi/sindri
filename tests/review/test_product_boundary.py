"""app/review/ ships to the client; app/eval/ holds gold-handling code that
does not (CLAUDE.md top matter: "They are deliberately separate -- eval never
imports pipeline internals that move under tuning"). The same separation must
hold for app/review: a stray `import app.eval` here would mean the review
package -- which the client's own machine runs -- pulls in code built around
the client's own gold corpus and scoring internals, which is a different
distribution problem than a stray import inside app/pipeline. This is a
static, structural guarantee, not a behavioural one: it is checked by parsing
the source, not by importing it (importing app.eval has its own heavy
dependencies this test must not require)."""
import ast
from pathlib import Path

REVIEW_DIR = Path(__file__).resolve().parents[2] / "app" / "review"


def _imports_app_eval(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "app.eval" or a.name.startswith("app.eval.")
                   for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == "app.eval"
                                 or node.module.startswith("app.eval.")):
                return True
    return False


def test_no_review_module_imports_app_eval():
    offenders = []
    for path in sorted(REVIEW_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _imports_app_eval(tree):
            offenders.append(path.name)
    assert offenders == []
