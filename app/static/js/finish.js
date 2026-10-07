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

  // What the SERVER last sealed, from /open (final_revision at sealed_seq).
  // `sealed` above is per-tab memory and a reload loses it, while store.seal
  // accepts a final_seq equal to the last sealed one -- so without this a
  // Finish with no new edits after a reopen would seal a redundant revision.
  // A null revision means nothing was ever sealed: nothing to remember.
  function seed({ sessionId, seq, revision }) {
    sealed = Number.isInteger(revision) && revision > 0 && Number.isInteger(seq)
      ? { sessionId, seq, revision }
      : null;
  }

  return { run, seed };
}

// The two downloads of one sealed revision, remembering which already
// arrived: a retry after the PDF failed should not hand the reviewer a
// second copy of the spreadsheet. The memory is per (session, revision) and
// is dropped once both files arrive, so a later Finish downloads both again.
// `download(sessionId, kind, revision)` is api.downloadExport.
export function createExporter(download, kinds = ['xlsx', 'pdf']) {
  let key = null;
  let done = new Set();
  return async function exportAll(snapshot, revision) {
    const k = `${snapshot.sessionId}:${revision}`;
    if (k !== key) { key = k; done = new Set(); }
    for (const kind of kinds) {
      if (done.has(kind)) continue;
      await download(snapshot.sessionId, kind, revision);
      done.add(kind);
    }
    key = null;
    done = new Set();
  };
}
