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

// A deletion is invisible to the since-cursor poll (GET /api/documents?since=
// only ever returns rows that CHANGED; a removed row has no updated_at to be
// newer than anything, so it would simply never be noticed missing). Every
// 20th poll (~1 minute at the 3 s cadence) does a full, since-less reload
// instead (decision 11). Pure, so the CADENCE is node-testable without a
// timer; the counter that drives it lives in the DOM half below.
export function shouldFullRefresh(pollCount) {
  return pollCount > 0 && pollCount % 20 === 0;
}

// ----- DOM (checked by import only; see tests/js/worklist.test.mjs) ---
let _docs = [];
let _cursor = null;
let _pollTimer = null;
let _selected = new Set();

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
  return tr;
}

function render() {
  const body = document.getElementById('wl-body');
  if (!body) return;
  const filtered = filterDocuments(_docs, currentFilters());
  body.innerHTML = '';
  for (const d of filtered) body.appendChild(row(d));
  const header = document.getElementById('wl-queue-header');
  if (header) header.textContent = formatQueueHeader(_lastQueue || { queued: 0, extracting: 0, paused: false, eta_seconds: null });
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
                               deleteDocument, downloadExport, pauseQueue, resumeQueue,
                               toast, badgesFor: workListBadgesFn, onOpenDoc } = {}) {
  // Every dependency is injected and every DOM lookup is guarded with `?.`:
  // this function is called from main.js at init() time, but the node
  // import-smoke test above calls it with none of index.html's elements
  // present (and no deps), and it must not throw either way.
  workListBadgesFor = workListBadgesFn || (() => []);
  docUrlFor = (d) => docUrl(d.id);

  async function refresh(full = false) {
    if (!listDocuments) return;
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
  document.getElementById('wl-bulk-remove')?.addEventListener('click', () => runBulk(deleteDocument, toast));
  document.getElementById('wl-bulk-export')?.addEventListener('click', () => runExport(downloadExport, toast));

  async function runBulk(fn, toastFn) {
    if (!fn) return;
    const ids = [..._selected];
    let failed = 0;
    for (const id of ids) {
      try { await fn(id); } catch { failed++; }
    }
    _selected = new Set();
    await refresh(true);
    if (failed) toastFn?.({ kind: 'warn', title: 'Some drawings were not changed', msg: `${failed} of ${ids.length} failed.` });
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

  refresh(true);
  startPolling();
  // getDocs: the review bar (Task 13) navigates over the SAME fetched list
  // rather than polling a second time, so Prev/Skip/Jump/Finish's "next
  // drawing" can never see a different snapshot than what the work list
  // itself is showing.
  return { refresh, getDocs: () => _docs };
}

let workListBadgesFor = () => [];
let docUrlFor = (d) => docUrl(d.id);
