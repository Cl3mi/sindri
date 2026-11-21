# Roadmap: Sindri

Goal: a ready application for working asynchronously through batches of technical drawings
(PDF drawings + Excel inspection sheets in the client's format). Extraction is automatic and
aims for the highest precision (every value delivered unflagged must be true). A person then
reviews it in the web frontend: they move, add or remove balloons, correct, add or remove
values, and check the table. Finishing the review exports the Excel sheet and the ballooned PDF.
Path to launch: a client test run on pre-processed drawings → measure every change the client
makes → fold the findings into the application → regular use with the same change measurement
running continuously to detect drift between what the application does and what the client wants.

## Now
- Operator: re-ingest gold with `--originals` (H2; the grader refuses pre-H2 gold)
- Operator: inspect the test-run campaign `pilot-1` locally (`campaign_ops.sh local-up`) and report UI issues

## Next
- Fix UI issues from the pilot inspection (each test-first, in its own worktree)
- Check that the change export answers the trend questions (per field, per kind, reasons, balloon/anchor moves in mm, adds/removes); extend it where it doesn't
- Client test run on the VPS: pre-processed drawings + Excel, client reviews and approves, then pull (`docs/deploy/vps.md`)
- Grade the test run (`score --reviewer-grade`), commit the counts-only digest
- Turn the test-run findings into a registered improvement plan and implement it
- Decide the import route for regular use (batch upload, already built in B2, vs a watched folder), after the findings are implemented
- HITL phase 2: cognitive forcing + audit sampling, rate set from the test-run digest (`2026-10-09-hitl-phase2-forcing-plan.md`)

## Later
- Launch for regular use (batch import → background extraction → review → Excel + ballooned PDF export)
- Continuous change measurement on regular reviews (HITL phase 3 grading), compared run over run to detect drift
- HITL phases 4-5: dev validation, return bundle
- Client's real review-cost weights (today's `weights.json` ranks recall over precision)
- High-confidence phantoms: ask the client what they leave unballooned, or learn that policy from train gold
- Retrain the read adapter after the `render_target` fix; price it under the active rules
- Show table rows in detection order until the first edit
- Cut detection cost (fewer/cheaper tiles, lower `max_new_tokens`)
- Multi-sheet and oversized drawings (out of scope today: 24 of 99 corpus drawings)
