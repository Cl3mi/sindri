# H2 / H3 hardening and the phase 2 plan refresh (2026-10-29)

Branch `h2-h3-hardening` (3 commits + this note), off `74ac86a`, NOT merged, NOT pushed.

* **H2** `0877860`: `ingest` writes `provenance["originals_space"]`; the grader and
  `score --reviewer-grade` refuse gold without it. Gold from before has no record:
  **re-ingest with `--originals` before grading the pilot**, or the grade run exits 1
  up front.
* **H3** `3a26c00`: no silent salt minting (see CLAUDE.md §2). Operator scripts
  `campaign_build.py` and `export_share.py` now stop with a traceback ending in the
  `SaltMissing` message on a machine without the salt; that is intended. Known gap:
  `verify` does not print predict's "throwaway ids" warning.
* **Phase 2 plan** `fede136`: refresh block at the top of
  `2026-10-09-hitl-phase2-forcing-plan.md`; gate = pilot graded first.
* Verification: full suite 2960 passed, 3 failed before the last fix; the one real
  failure (`test_anon`, my env-salt interaction) is fixed, the other two are the
  known `test_title_block` environmental pair.
