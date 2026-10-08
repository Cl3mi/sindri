// Mass export (batch design §4): pure status text, cadence, URLs and error
// copy, kept separate from the DOM wiring in worklist.js so they are
// node-testable with no fetch mock at all and reusable from the work
// list's own bulk "Export selected" action.

export function exportStatusText(job) {
  if (job.state === 'failed') return 'Export failed';
  if (job.state === 'done') return 'Export ready';
  return `Exporting ${job.done} / ${job.total}…`;
}

// Same cadence as the work list's own 3 s poll (design §3) -- one rhythm
// for the whole screen, not a second timer ticking at its own rate.
export function pollIntervalMs() {
  return 3000;
}

export function downloadUrl(exportId) {
  return `/api/exports/${exportId}/download`;
}

// error_type is either a fixed tag the server itself chose on purpose
// (app/batch/exports.py: "interrupted", "OutOfDisk") or an arbitrary
// exception class name (its "anything else" branch) -- only a tag this
// module actually knows the meaning of earns its own sentence; everything
// else falls back to the generic one rather than surfacing a class name to
// the reviewer (CLAUDE.md §1: fixed sentences, never exception text).
export function exportFailureMessage(errorType) {
  if (errorType === 'OutOfDisk') return 'Export failed — not enough disk space.';
  return 'Export failed — try again.';
}

// The ZIP's 24h cleanup sweep (Task 7) can expire a job between "done" and
// the reviewer clicking download; app/main.py's download route answers 410
// for exactly that case (distinct from the transient 409s the same route
// gives while the job is still running or failed outright).
export function downloadFailureMessage(status) {
  if (status === 410) return 'export expired — export again';
  return 'Export failed — try again.';
}
