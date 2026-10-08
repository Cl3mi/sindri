// Review-bar navigation (batch design §3). Pure: `docs` is the same array
// the work list already polls (app/static/js/worklist.js), filtered here to
// one batch's reviewable drawings. "List order" is the array's own order
// (server-sorted by rowid, the true staging order -- app/batch/repo.py's
// list_documents; see this plan's decision 8).

const REVIEWABLE = (d) => d.state === 'ready' || d.state === 'in_review';

// Normalises the several shapes an "ids to exclude" argument can take --
// nothing, a bare id, an array, or a Set -- into one Set, so every function
// below accepts whichever is convenient at its call site without the
// caller wrapping it first. Two different callers need to exclude two
// different things (the drawing already open; every id the client has
// just sealed this session, batch-async-review.md's `recentlyFinished`),
// and both need to compose into one filter.
function toExcludeSet(excludeIds) {
  if (!excludeIds) return new Set();
  if (excludeIds instanceof Set) return new Set(excludeIds);
  if (Array.isArray(excludeIds)) return new Set(excludeIds);
  return new Set([excludeIds]);
}

export function navOrder(docs, batchId, excludeIds) {
  const exclude = toExcludeSet(excludeIds);
  return docs.filter((d) => d.batch_id === batchId && REVIEWABLE(d) && !exclude.has(d.id));
}

export function prevId(docs, batchId, currentId, excludeIds) {
  const order = navOrder(docs, batchId, excludeIds);
  const i = order.findIndex((d) => d.id === currentId);
  return i > 0 ? order[i - 1].id : null;
}

// Mirrors prevId for "Skip for now", which moves forward without changing
// anything (design §3): same list, next entry, or null at the end.
export function nextId(docs, batchId, currentId, excludeIds) {
  const order = navOrder(docs, batchId, excludeIds);
  const i = order.findIndex((d) => d.id === currentId);
  return i >= 0 && i < order.length - 1 ? order[i + 1].id : null;
}

// After Finish (design §3): the next ready/in-review drawing in the SAME
// batch, else the first ready/in-review drawing in ANY batch, else
// "extracting" if something is still on its way (queued or extracting,
// anywhere), else "done". `finishedId` is always excluded -- in principle
// its state has already moved past ready/in_review by the time this runs,
// but the client's own poll window can still show it stale (the bug this
// plan's "navigation never returns to a drawing just finished" fix
// closes) -- and `excludeIds` lets the caller add any OTHER id it does not
// trust yet (an earlier finish still unconfirmed in the cache).
export function nextAfterFinish(docs, batchId, finishedId, excludeIds) {
  const exclude = toExcludeSet(excludeIds);
  exclude.add(finishedId);
  const sameBatch = navOrder(docs, batchId, exclude);
  if (sameBatch.length) return { status: 'next', id: sameBatch[0].id };
  const anyBatch = jumpCandidates(docs, exclude);
  if (anyBatch.length) return { status: 'next', id: anyBatch[0].id };
  const onTheWay = docs.some((d) => d.state === 'queued' || d.state === 'extracting');
  return onTheWay ? { status: 'extracting' } : { status: 'done' };
}

// excludeIds: the drawing already open and/or every id just sealed this
// session, when there are any. Re-offering the open drawing as a Jump
// target would let a reviewer "jump" to the drawing they are already on,
// which re-/opens it -- a fresh /open mints a new writer and
// journal.resume() wipes `pending`, silently losing unflushed edits
// (reopen.js's shouldReopen is the defense-in-depth half of that fix).
// Re-offering a just-finished drawing is the SAME shape of bug one step
// later: the client's own poll window can still list it as ready/in_review
// after Finish has already sealed it server-side.
export function jumpCandidates(docs, excludeIds) {
  const exclude = toExcludeSet(excludeIds);
  return docs.filter((d) => REVIEWABLE(d) && !exclude.has(d.id));
}

// The Jump (Ctrl+K) palette's type-to-filter: case-insensitive substring on
// file name, over whatever list the caller already narrowed down (normally
// jumpCandidates(docs)). An empty/missing query matches everything, same as
// filterDocuments' search field (worklist.js) and filterJump never changes
// the given order -- the palette's own ↑/↓ relies on that staying List order.
export function filterJump(candidates, query) {
  const q = (query || '').toLowerCase();
  if (!q) return candidates;
  return candidates.filter((d) => d.file_name.toLowerCase().includes(q));
}

// "Skip for now" (design §3): the next reviewable drawing after the current
// one in the SAME batch, falling back to any other batch's -- the same
// fallback nextAfterFinish uses, since running out of drawings to skip to
// in one batch is not meaningfully different from finishing it. Takes
// `navigate` (router.js's seam) and `docUrl` as dependencies and calls
// `navigate` itself, rather than returning an id for the caller to write
// into the hash with a plain setHash() -- that shape of call is exactly
// the bug decision 10 fixes (history.replaceState alone never dispatches),
// so there is no id-only return path left for a caller to misuse the same
// way again. Returns the id it navigated to, or null if there was nowhere
// to go (and navigate is then never called at all). `excludeIds` (a
// single id, array or Set) is composed with `currentId` for the cross-batch
// fallback -- `currentId` itself is NOT removed from the same-batch list
// nextId reads, which still needs it present to find its own position.
export function skip(docs, batchId, currentId, { navigate: nav, docUrl, excludeIds } = {}) {
  const fallbackExclude = toExcludeSet(excludeIds);
  fallbackExclude.add(currentId);
  const id = nextId(docs, batchId, currentId, excludeIds)
    || jumpCandidates(docs, fallbackExclude)[0]?.id
    || null;
  if (id) nav(docUrl(id));
  return id;
}

// Finish's "extracting" chain-to-next toast (main.js): the queue's
// `eta_seconds` (app.batch.repo.queue_summary / the worker's rolling
// median) is CUMULATIVE over every queued drawing, not one drawing's wait
// -- quoting it whole would overstate "the next drawing" by a factor of
// however many are ahead of it. Dividing by `queued` gives the per-drawing
// estimate the toast actually promises, clamped so it is never 0 min
// (true, but reads as "basically now", which it may not be) and never
// divides by zero when nothing is queued yet (its own eta is one
// drawing's already). `eta_seconds == null` means no sample exists yet
// (worker.py: a fresh process with no completed extraction) -- CLAUDE.md
// §4 forbids a number with no basis, so that case gets words, not a
// guessed 0 or a stale number.
export function nextReadyCopy(queue) {
  if (!queue || queue.eta_seconds == null) {
    return { title: 'Next drawing is still extracting', msg: 'Estimating how long it will take.' };
  }
  const perDrawingMin = Math.max(1, Math.round(queue.eta_seconds / Math.max(queue.queued, 1) / 60));
  return { title: `Next drawing ready in ≈ ${perDrawingMin} min`,
           msg: 'Still extracting — the work list will show it.' };
}
