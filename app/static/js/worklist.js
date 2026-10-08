// Work list: client-side filtering of an already-fetched page, and the
// queue header's text (batch design §3). The server applies the same
// filters on GET /api/documents (app/batch/repo.py list_documents) -- this
// is what a typed search or a clicked pill applies instantly, between polls.

export function filterDocuments(docs, { batchId, state, search, needsMe } = {}) {
  let out = docs;
  if (batchId) out = out.filter((d) => d.batch_id === batchId);
  if (state) out = out.filter((d) => d.state === state);
  if (needsMe) out = out.filter((d) => d.state === 'ready' || d.state === 'in_review');
  if (search) {
    const q = search.toLowerCase();
    out = out.filter((d) => d.file_name.toLowerCase().includes(q));
  }
  return out;
}

// "~2h 10m left" / "~2m left" / "estimating…" (no history yet: worker.py's
// eta_seconds is null with zero samples -- never a number built from none).
export function formatEta(seconds) {
  if (seconds == null) return 'estimating…';
  const total = Math.max(0, Math.round(seconds / 60));
  const h = Math.floor(total / 60);
  const m = total % 60;
  return h > 0 ? `~${h}h ${m}m left` : `~${m}m left`;
}

export function formatQueueHeader(q) {
  if (q.paused) return `Paused · ${q.queued} queued`;
  if (q.queued === 0 && q.extracting === 0) return 'Queue idle';
  return `Extracting · ${q.queued} queued · ${formatEta(q.eta_seconds)}`;
}
