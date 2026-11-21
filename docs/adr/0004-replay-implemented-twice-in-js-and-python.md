# 0004. Review state transitions are implemented twice (browser JS and server Python), held together by a fuzz test

Status: Accepted
Date: 2026-10-07

## Context

The reviewer UI must apply every operation instantly (move a balloon, edit a cell, undo),
so the state machine lives in the browser (`app/static/js/state.js`). But the seal must not
trust the browser: it replays the net journal on the server-held proposal and compares the
result to the client's final rows (`app/review/replay.py`, HITL design §2 and the phase-1
plan, Task 2). If the two disagree, grading's ground truth is wrong.

The front end is deliberately dependency-free ES modules with no build step, and the
product runs on the client's own machine, so adding a bundler or a shared-runtime layer
was not free.

## Decision

Keep **two implementations**: `state.js` ops in the browser, `replay.py` on the server.
`replay.py` documents that "the semantics mirror `state.js` operation by operation; a
change to an op there must change its branch here". The contract is enforced by
`tests/review/test_journal_replay_parity.py`, which fuzzes the JS↔Python replay
directly, plus node tests for the pure UI rules that run inside pytest (and skip without
node). The seal reports mismatches by row id and refuses to seal on them.

## Alternatives considered

The source documents do not list rejected alternatives for this decision. The following
are *reconstructed* from the constraints, not quoted:

- **Server-authoritative state, UI as a thin client** (one round trip per operation) —
  loses instant feedback and undo, and makes a flaky network a review blocker.
- **One implementation executed in both places** (running JS under node on the server, or
  transpiling Python to JS) — adds a runtime or build step to a product whose front end
  intentionally has neither.

## Consequences

Easier: a server-side seal with no JS runtime; a UI with no build chain; grading that can
run in a plain Python process.

Accepted costs:

- **Every new operation touches four places**: `journal.py` (validation and vocabulary),
  `replay.py`, `state.js`, and the fuzz. C1 added `move_target`, `set_note`, closed
  reasons and `flag` this way; the fuzz was extended in the same commits ("the replay
  parity fuzz drives anchors, notes and reasons").
- **Vocabulary is mirrored by hand**: the reason keys in `reasons.js` match `journal.py`,
  pinned by a node test ("pin the UI's reason vocabularies and note limit to the
  journal").
- The fuzz proves only what it generates; an operation the fuzz does not drive can drift
  silently until a real seal disagrees.

## Lessons

- A real-browser check caught what unit tests could not (H8): the journaled balloon
  position was never wrong during a wheel-zoom drag, but a re-render made it look wrong.
  The earlier guess that the release position was off was incorrect; measure before
  fixing. Parity tests cover the state contract, not layout behaviour — that is why
  `tests/browser/` exists.
- Subtle: `note_of` refuses raw journals and the anchor round trip is pinned at seal, both
  added after parity gaps surfaced during C1.
- Related: [0002](0002-journal-and-sealed-revisions-as-source-of-truth.md).
