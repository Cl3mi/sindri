// Work list: client-side filtering of an already-fetched page, and the
// queue header's text (batch design §3). The server applies the same
// filters on GET /api/documents (app/batch/repo.py list_documents) -- this
// is what a typed search or a clicked pill applies instantly, between polls.

import { docUrl } from './router.js';

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

// The #wl-pause-resume button's own label, independent of the queue-header
// text above: render() used to set the header from `_lastQueue` but never
// touched this button, so it always read "Pause" even once the queue really
// was paused -- the click handler's toggle itself was always correct (it
// reads `_lastQueue?.paused` directly), but nothing on screen ever told the
// reviewer which action the button would take next. `queue` is null before
// the first `GET /api/documents` reply lands, which must read as the
// not-paused default rather than throw on `queue.paused`.
export function pauseButtonLabel(queue) {
  return queue?.paused ? 'Resume' : 'Pause';
}

// A deletion is invisible to the since-cursor poll (GET /api/documents?since=
// only ever returns rows that CHANGED; a removed row has no updated_at to be
// newer than anything, so it would simply never be noticed missing). Every
// 20th poll (~1 minute at the 3 s cadence) does a full, since-less reload
// instead (decision 11). Pure, so the CADENCE is node-testable without a
// timer; the counter that drives it lives in the DOM half below.
export function shouldFullRefresh(pollCount) {
  return pollCount > 0 && pollCount % 20 === 0;
}

// Pure half of initWorkList's markDoc: a new array with `id`'s entry
// shallow-merged with `patch`, or the SAME array back (no copy) when `id`
// is not present -- a caller can compare by reference to tell "nothing
// changed" from "changed, but to the same values" if it ever needs to.
// Exists so a client-caused state change (Finish sealing a drawing, a
// reextract, a removal) can be reflected in the cache INSTANTLY, rather
// than waiting on the next 3 s poll tick or an awaited refresh that may
// itself still be in flight -- reviewbar.js's nextAfterFinish/skip/jump
// must never read a just-finished drawing as still ready/in_review.
export function applyPatch(docs, id, patch) {
  const idx = docs.findIndex((d) => d.id === id);
  if (idx === -1) return docs;
  const next = docs.slice();
  next[idx] = { ...next[idx], ...patch };
  return next;
}

// Bulk routes under api.js are not uniform: moveToFront/cancelDocument/
// retryDocument throw (jsonOrThrow) on a refusal, but deleteDocument is
// BEST-EFFORT and never throws -- it resolves { ok: false, status } instead
// (so Stop's own retry loop can tell "gone" from "hiccup" without a catch).
// A bulk loop that only counts thrown errors therefore swallows every
// refused delete (409: the drawing is still extracting) as a silent
// success, and the row simply stays in the list with no explanation
// (review finding on 84168f4). `runBulk` below always normalises its
// per-row outcome into this same { ok, status } shape before tallying, so
// this pure function is the single place "did this row fail" is decided.
//
// A 409 means something different per action -- deleteDocument's is "the
// worker still holds it", reextractDocument's is "has_events() is true, a
// human already reviewed it" (design §2's "ready with no review events"
// clause) -- so the message is keyed by action, not just by status. No
// action given (the original shape, kept so existing callers/tests are
// unaffected) falls back to 'default', which reads the same as the
// original delete-only message.
const BULK_FAIL_MESSAGES = {
  default: { 409: 'still extracting — cancel it first' },
  deleteDocument: { 409: 'still extracting — cancel it first' },
  reextractDocument: { 409: 'already reviewed — re-extract only works on untouched drawings' },
};

export function summarizeBulkFailures(results, action = 'default') {
  const messages = BULK_FAIL_MESSAGES[action] || {};
  const failed = results.filter((r) => r && r.ok === false);
  if (!failed.length) return null;
  const byStatus = new Map();
  for (const r of failed) byStatus.set(r.status, (byStatus.get(r.status) || 0) + 1);
  const text = [...byStatus.entries()]
    .map(([status, n]) => `${n} ${messages[status] || `failed${status ? ` (${status})` : ''}`}`)
    .join('; ');
  return { count: failed.length, text };
}

// ----- DOM (checked by import only; see tests/js/worklist.test.mjs) ---
let _docs = [];
let _cursor = null;
let _pollTimer = null;
let _selected = new Set();
let _refreshing = false;   // an in-flight GET /api/documents
let _refreshAgain = false; // a refresh was asked for while one was already running

function row(d) {
  const tr = document.createElement('tr');
  tr.dataset.id = d.id;
  const checked = _selected.has(d.id) ? 'checked' : '';
  const badges = [...workListBadgesFor(d)].map((b) => b.text).join(' · ');
  const link = document.createElement('a');
  link.href = docUrlFor(d);
  link.textContent = d.file_name;
  tr.innerHTML = `
    <td class="check"><input type="checkbox" class="wl-row-check" ${checked} /></td>
    <td class="wl-file"></td>
    <td class="wl-batch"></td>
    <td><span class="state-chip state-${d.state}">${d.state.replaceAll('_', ' ')}</span></td>
    <td>${d.flagged_open ?? '—'}/${d.flagged_total ?? '—'}</td>
    <td>${d.unchecked ?? '—'}</td>
    <td class="wl-updated"></td>
    <td class="muted wl-badges"></td>`;
  // Every value that comes from the server (file name, rel path, batch
  // name, timestamp, badge text) is set via textContent, never interpolated
  // into innerHTML -- a client's own file names or batch names must never
  // be able to inject markup (B3 plan Task 10 requirement).
  const fileCell = tr.querySelector('.wl-file');
  fileCell.appendChild(link);
  if (d.rel_path) {
    const rel = document.createElement('span');
    rel.className = 'muted';
    rel.textContent = ` ${d.rel_path}`;
    fileCell.appendChild(document.createTextNode(' '));
    fileCell.appendChild(rel);
  }
  tr.querySelector('.wl-batch').textContent = d.batch_name || '';
  tr.querySelector('.wl-updated').textContent = d.updated_at || '';
  tr.querySelector('.wl-badges').textContent = badges;
  // setAttribute never parses its value as markup (unlike innerHTML), so a
  // file name with quotes or angle brackets in it is still safe here.
  tr.querySelector('.wl-row-check')?.setAttribute('aria-label', `Select ${d.file_name}`);
  return tr;
}

// A selection must never outlive the row it points at: a drawing removed
// by another tab (or by this tab's own bulk Remove) would otherwise leave a
// phantom entry in `_selected`, counted in the bulk bar and resurrected the
// next time "select all" is pressed.
function pruneSelected() {
  const ids = new Set(_docs.map((d) => d.id));
  for (const id of _selected) if (!ids.has(id)) _selected.delete(id);
}

function render() {
  const body = document.getElementById('wl-body');
  if (!body) return;
  pruneSelected();
  const filtered = filterDocuments(_docs, currentFilters());
  body.innerHTML = '';
  for (const d of filtered) body.appendChild(row(d));
  const header = document.getElementById('wl-queue-header');
  if (header) header.textContent = formatQueueHeader(_lastQueue || { queued: 0, extracting: 0, paused: false, eta_seconds: null });
  const pauseBtn = document.getElementById('wl-pause-resume');
  if (pauseBtn) pauseBtn.textContent = pauseButtonLabel(_lastQueue);
  updateBulkBar();
}

let _lastQueue = null;

function currentFilters() {
  return {
    batchId: document.getElementById('wl-batch-filter')?.value || undefined,
    state: document.getElementById('wl-state-filter')?.value || undefined,
    search: document.getElementById('wl-search')?.value || undefined,
    needsMe: document.getElementById('wl-needs-me')?.classList.contains('active') || false,
  };
}

function updateBulkBar() {
  const bar = document.getElementById('wl-bulk');
  if (!bar) return;
  bar.hidden = _selected.size === 0;
  const count = document.getElementById('wl-bulk-count');
  if (count) count.textContent = `${_selected.size} selected`;
}

export function initWorkList({ listDocuments, moveToFront, cancelDocument, retryDocument,
                               deleteDocument, reextractDocument, downloadExport, pauseQueue,
                               resumeQueue, toast, badgesFor: workListBadgesFn, onOpenDoc } = {}) {
  // Every dependency is injected and every DOM lookup is guarded with `?.`:
  // this function is called from main.js at init() time, but the node
  // import-smoke test above calls it with none of index.html's elements
  // present (and no deps), and it must not throw either way.
  workListBadgesFor = workListBadgesFn || (() => []);
  docUrlFor = (d) => docUrl(d.id);

  document.getElementById('wl-search')?.setAttribute('aria-label', 'Search by file name');
  document.getElementById('wl-select-all')?.setAttribute('aria-label', 'Select all');
  document.getElementById('wl-batch-filter')?.setAttribute('aria-label', 'Filter by batch');
  document.getElementById('wl-state-filter')?.setAttribute('aria-label', 'Filter by state');

  // Two pollers (the 3 s tick and a visibilitychange firing mid-request)
  // must never race: a reply landing out of order could merge an older
  // `_cursor` over a newer one and quietly resurrect a row just removed. A
  // refresh asked for while one is already in flight is deferred -- not
  // dropped -- by setting `_refreshAgain` and running once more, as a FULL
  // reload, the moment the in-flight one finishes.
  async function refresh(full = false) {
    if (!listDocuments) return;
    if (_refreshing) { _refreshAgain = true; return; }
    _refreshing = true;
    try {
      const body = await listDocuments(full ? {} : { since: _cursor || undefined });
      if (full) _docs = body.documents;
      else {
        const byId = new Map(_docs.map((d) => [d.id, d]));
        for (const d of body.documents) byId.set(d.id, d);
        _docs = [...byId.values()];
      }
      _cursor = body.cursor;
      _lastQueue = body.queue;
      render();
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not refresh the work list', msg: String(err?.message || err) });
    } finally {
      _refreshing = false;
      if (_refreshAgain) {
        _refreshAgain = false;
        refresh(true);
      }
    }
  }

  document.getElementById('wl-search')?.addEventListener('input', () => render());
  document.getElementById('wl-batch-filter')?.addEventListener('change', () => render());
  document.getElementById('wl-state-filter')?.addEventListener('change', () => render());
  document.getElementById('wl-needs-me')?.addEventListener('click', (e) => {
    e.currentTarget.classList.toggle('active');
    render();
  });
  document.getElementById('wl-select-all')?.addEventListener('change', (e) => {
    _selected = e.target.checked ? new Set(filterDocuments(_docs, currentFilters()).map((d) => d.id)) : new Set();
    render();
  });
  document.getElementById('wl-body')?.addEventListener('change', (e) => {
    if (!e.target.classList.contains('wl-row-check')) return;
    const id = e.target.closest('tr')?.dataset.id;
    if (!id) return;
    if (e.target.checked) _selected.add(id); else _selected.delete(id);
    updateBulkBar();
  });
  document.getElementById('wl-pause-resume')?.addEventListener('click', async (e) => {
    try {
      if (_lastQueue?.paused) await resumeQueue?.(); else await pauseQueue?.();
      await refresh(true);
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not change the queue', msg: String(err?.message || err) });
    }
  });
  document.getElementById('wl-bulk-front')?.addEventListener('click', () => runBulk(moveToFront, toast));
  document.getElementById('wl-bulk-cancel')?.addEventListener('click', () => runBulk(cancelDocument, toast));
  document.getElementById('wl-bulk-retry')?.addEventListener('click', () => runBulk(retryDocument, toast));
  document.getElementById('wl-bulk-remove')?.addEventListener('click', () => runBulk(deleteDocument, toast, 'deleteDocument'));
  document.getElementById('wl-bulk-reextract')?.addEventListener('click', () => {
    // Design §2: re-extract discards the drawing's current proposal, so a
    // bulk run over several rows (picked once, not one at a time) asks
    // before doing anything a reviewer cannot undo from this screen.
    const n = _selected.size;
    if (!n) return;
    if (!confirm(`Re-extract ${n} drawing${n === 1 ? '' : 's'}? Their current proposals are discarded.`)) return;
    runBulk(reextractDocument, toast, 'reextractDocument');
  });
  document.getElementById('wl-bulk-export')?.addEventListener('click', () => runExport(downloadExport, toast));

  async function runBulk(fn, toastFn, action = 'default') {
    if (!fn) return;
    const ids = [..._selected];
    const results = [];
    for (const id of ids) {
      try {
        const r = await fn(id);
        // Most routes (moveToFront/cancelDocument/retryDocument) throw on a
        // refusal and resolve the server's JSON on success; deleteDocument
        // is best-effort and resolves { ok, status } either way. Normalise
        // both shapes to the same thing summarizeBulkFailures reads, so a
        // route that quietly says "no" is never mistaken for a success.
        results.push(r && typeof r === 'object' && r.ok === false ? r : { ok: true });
      } catch (err) {
        results.push({ ok: false, status: err?.status ?? 0 });
      }
    }
    _selected = new Set();
    await refresh(true);
    const summary = summarizeBulkFailures(results, action);
    if (summary) {
      toastFn?.({ kind: 'warn', title: 'Some drawings were not changed',
                  msg: `${summary.count} of ${ids.length} failed: ${summary.text}.` });
    }
  }

  async function runExport(downloadFn, toastFn) {
    if (!downloadFn) return;
    const ids = [..._selected].filter((id) => _docs.find((d) => d.id === id)?.state === 'finished');
    const skipped = _selected.size - ids.length;
    for (const id of ids) {
      try { await downloadFn(id, 'xlsx', _docs.find((d) => d.id === id).final_revision); } catch { /* per-file failure; continue */ }
      try { await downloadFn(id, 'pdf', _docs.find((d) => d.id === id).final_revision); } catch { /* per-file failure; continue */ }
    }
    if (skipped) toastFn?.({ kind: 'warn', title: 'Some drawings were skipped',
                             msg: `${skipped} selected drawing(s) are not finished yet.` });
  }

  let _pollCount = 0;
  function startPolling() {
    clearTimeout(_pollTimer);
    const tick = async () => {
      if (!document.hidden) {
        _pollCount++;
        await refresh(shouldFullRefresh(_pollCount));
      }
      _pollTimer = setTimeout(tick, 3000);
    };
    tick();
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refresh();
  });

  // The DOM-wiring half of applyPatch: updates the mutable cache in place
  // and re-renders, so a client-caused state change (Finish sealing a
  // drawing, a reextract, a removal) shows up on screen the same tick,
  // not only after the next poll or an awaited refresh.
  function markDoc(id, patch) {
    _docs = applyPatch(_docs, id, patch);
    render();
  }

  refresh(true);
  startPolling();
  // getDocs: the review bar (Task 13) navigates over the SAME fetched list
  // rather than polling a second time, so Prev/Skip/Jump/Finish's "next
  // drawing" can never see a different snapshot than what the work list
  // itself is showing. getQueue: Finish's "extracting" toast (reviewbar.js's
  // nextReadyCopy) needs the same queue summary this screen's own header
  // already shows, rather than a second request for the same numbers.
  // markDoc: Finish's success path (main.js) marks the just-sealed drawing
  // 'finished' here BEFORE awaiting an authoritative refresh, closing the
  // window where nextAfterFinish could otherwise read it as still
  // ready/in_review from the client's own stale cache.
  return { refresh, getDocs: () => _docs, getQueue: () => _lastQueue, markDoc };
}

let workListBadgesFor = () => [];
let docUrlFor = (d) => docUrl(d.id);
