"""Review records: what the system proposed, what the inspector did, and the
sealed final (docs/plans/2026-10-07-hitl-review-grading-design.md).

Product code -- it ships to the client. It must never import app.eval, which
holds gold-handling code that does not."""
