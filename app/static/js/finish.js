// Finish & Export (docs/plans/2026-10-07-hitl-review-grading-design.md §2):
// flush the journal, seal the review, export THAT revision from the server.
// Pure apart from the injected functions, so node can test it.
//
// One review pass must seal exactly one revision -- grading reads the sealed
// revisions, and a redundant sealed/rN.json for an unchanged journal is noise
// it would have to explain away. So a run is single-flight (a double click
// does nothing), and a journal position already sealed in this session is
// never sealed again: a retry after a failed export only re-exports. Any
// further edit moves lastSeq, which seals a new revision as designed.

export function createFinisher({ journal, seal, exportAll, canFinish }) {
  let running = false;
  let sealed = null;   // { sessionId, seq, revision } of the last successful seal

  async function run(snapshot) {
    // Checked and set before the first await, so a second call in the same
    // tick already sees it.
    if (running) return { status: 'busy' };
    if (!canFinish()) return { status: 'blocked' };
    running = true;
    try {
      const { sessionId, rows, reviewedIds } = snapshot;
      if (sealed && sealed.sessionId !== sessionId) sealed = null;
      // The journal is mandatory (batch design decision 30): a review the
      // server cannot replay is never exported.
      if (!journal.active) throw new Error('This drawing has no review log — reopen it to continue.');
      // Read once, with the snapshot: rows and final_seq must describe the
      // same moment. An edit made mid-run lands above it, and the server
      // refuses that seal rather than recording a replay mismatch.
      const seq = journal.lastSeq;
      const acked = await journal.flush();
      if (acked < seq) throw new Error('The review log is still saving — try again in a moment.');
      let revision;
      if (sealed && sealed.seq === seq) {
        revision = sealed.revision;
      } else {
        const res = await seal(sessionId, {
          writer: journal.writer, final_seq: seq, rows, reviewed_ids: reviewedIds,
        });
        revision = res.revision;
        sealed = { sessionId, seq, revision };
      }
      await exportAll(snapshot, revision);
      return { status: 'done', revision };
    } finally {
      running = false;
    }
  }

  return { run };
}
