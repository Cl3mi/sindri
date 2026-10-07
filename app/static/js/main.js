// Entry point: wires modules, health check, file upload, the queue wait,
// reopening a drawing from its link, and Finish & Export.

import { state, on, restoreSession, clearSession, undo, redo,
         canUndo, canRedo, progress, canFinish } from './state.js';
import { savePdf, getDocument, enqueueDocument, cancelDocument, openDocument,
         deleteDocument, downloadExport, health, postEvents, beaconEvents,
         sealSession } from './api.js';
import { createJournal } from './journal.js';
import { createFinisher, createExporter } from './finish.js';
import { restoreReview, flushBeforeLeaving } from './reopen.js';
import { initViewer } from './viewer.js';
import { initTable } from './table.js';
import {
  toast, initModal, openHelp, initSplitter,
  initThemeAndDensity, initDragDrop,
} from './ui.js';
import { initShortcuts } from './shortcuts.js';

// The drawing this tab is waiting on (staged or extracting). Extraction runs
// on the server; closing the tab no longer stops it (batch design §1).
let waitingOn = null;     // { id, fileName, pages }
let pollTimer = null;
let stopping = false;     // Stop pressed; waiting for the worker to let go

// The review journal: every state operation, numbered and flushed to the
// server (docs/plans/2026-10-07-hitl-review-grading-design.md §2). It is
// mandatory (batch design decision 30), so there is no "logging off" mode.
const journal = createJournal({
  send: (sid, writer, events) => postEvents(sid, writer, events),
});
let flushTimer = null;
let flushBackoff = 500;

// Flush → seal → export, single-flight, never re-sealing an unchanged
// journal (finish.js) -- including after a reload, via seed() on open.
const finisher = createFinisher({
  journal,
  canFinish,
  seal: (sid, body) => sealSession(sid, body),
  // Remembers which file of a revision already arrived, so a retry after a
  // failed PDF does not download the spreadsheet a second time.
  exportAll: createExporter((sid, kind, revision) => downloadExport(sid, kind, revision)),
});

function init() {
  initThemeAndDensity();
  initSplitter();
  initModal();
  initViewer();
  initTable();
  initShortcuts();
  initDragDrop(handleFile);
  wireHeader();
  wireFooter();
  wireFileInputs();
  wireExtractionControls();
  wireFinish();
  wireJournal();
  wireUndoRedo();
  pingHealth();
  resumeFromHash();
}

// ===== Link / reload =================================================
// #doc=<id> keeps the drawing addressable, so a reload -- or coming back
// tomorrow with the same link -- reopens it where the reviewer stopped.
function setHash(id) { history.replaceState(null, '', id ? `#doc=${id}` : '#'); }
function hashDoc() { return /^#doc=([0-9a-f]{32})$/.exec(location.hash)?.[1] || null; }

async function resumeFromHash() {
  const id = hashDoc();
  if (!id) return;
  try {
    const doc = await getDocument(id);
    routeDocument(doc);
  } catch {
    setHash(null);
  }
}

function routeDocument(doc) {
  waitingOn = { id: doc.id, fileName: doc.file_name, pages: doc.pages };
  if (doc.state === 'staged') showConfirm(doc.file_name, doc.pages);
  else if (doc.state === 'queued' || doc.state === 'extracting') { showExtracting(doc.file_name); pollUntilReady(); }
  else if (['ready', 'in_review', 'finished'].includes(doc.state)) loadForReview(doc.id);
  else {
    toast({ kind: 'error', title: `Drawing ${doc.state}`,
            msg: doc.error_type ? `Extraction failed (${doc.error_type}).` : 'Upload it again to retry.' });
    waitingOn = null;
    hideExtracting();
    backToEmptyOrCurrent();
  }
}

// After backing out of a drawing: the review still on screen (a drawing
// dropped while reviewing another), else the empty state.
function backToEmptyOrCurrent() {
  setHash(state.sessionId || null);
  if (state.sessionId) {
    document.getElementById('plan-confirm').hidden = true;
    document.getElementById('plan-extracting').hidden = true;
  } else {
    backToEmpty();
  }
}

// ===== Header / file chip ============================================
function wireHeader() {
  document.getElementById('file-close').addEventListener('click', () => {
    if (!confirm('Close this drawing? Your review is saved and reopens from its link.')) return;
    // clearSession() below wipes sessionId/writer out of the journal's
    // reach (start() on the NEXT drawing resets it anyway) -- so any event
    // still only in `pending` must go out now or it is gone for good. The
    // events are idempotent server-side (append is by seq), so a close that
    // races an in-flight flush double-sends at worst, never drops.
    if (journal.active && journal.pending.length) {
      beaconEvents(journal.sessionId, journal.writer, journal.pending);
    }
    clearSession();
    setHash(null);
  });
  on('session', () => {
    const chip  = document.getElementById('file-chip');
    const name  = document.getElementById('file-name');
    if (state.sessionId) {
      chip.hidden = false;
      name.textContent = state.fileName || 'drawing.pdf';
      document.getElementById('finish-btn').disabled = false;
    } else {
      chip.hidden = true;
      const fin = document.getElementById('finish-btn');
      fin.disabled = true;
      // The 'change' handler skips a cleared session, so reset here or the
      // next drawing opens with the last one's "N flagged rows" state.
      fin.classList.remove('blocked');
      fin.title = 'Seal the review and export Excel + ballooned PDF';
    }
  });
}

function wireFooter() {
  document.getElementById('help-toggle').addEventListener('click', openHelp);
}

// ===== File input handling ==========================================
function wireFileInputs() {
  const main  = document.getElementById('file-input');
  const empty = document.getElementById('file-input-empty');
  main.addEventListener('change',  (e) => e.target.files[0] && handleFile(e.target.files[0]));
  empty.addEventListener('change', (e) => e.target.files[0] && handleFile(e.target.files[0]));
}

async function handleFile(file) {
  if (!file) return;
  // One waiting drawing per tab: a second upload would orphan the first,
  // which only its link could ever reach again (B3 adds a list).
  if (waitingOn) {
    toast({ kind: 'warn', title: 'A drawing is already waiting',
            msg: 'Cancel or stop it before opening another drawing.' });
    return;
  }
  if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
    toast({ kind: 'warn', title: 'Unsupported file', msg: 'Please drop a .pdf' });
    return;
  }
  setBusy(`Opening ${file.name}…`);
  try {
    const meta = await savePdf(file);
    waitingOn = { id: meta.session_id, fileName: meta.fileName || file.name, pages: meta.pages };
    setHash(meta.session_id);
    showConfirm(waitingOn.fileName, meta.pages);
  } catch (err) {
    toast({ kind: 'error', title: 'Could not open PDF', msg: String(err.message || err) });
  } finally {
    setIdle();
  }
}

// ===== Confirm → Queue → Wait → Open ================================
function wireExtractionControls() {
  document.getElementById('cf-start').addEventListener('click', startExtraction);
  document.getElementById('cf-cancel').addEventListener('click', cancelConfirm);
  document.getElementById('ex-stop').addEventListener('click', stopExtraction);
}

function showConfirm(fileName, pages) {
  document.getElementById('plan-empty').hidden = true;
  document.getElementById('plan-extracting').hidden = true;
  document.getElementById('cf-file').textContent = fileName;
  document.getElementById('cf-pages').textContent =
    `${pages} page${pages === 1 ? '' : 's'} · ready to extract`;
  document.getElementById('plan-confirm').hidden = false;
}

function backToEmpty() {
  document.getElementById('plan-confirm').hidden = true;
  document.getElementById('plan-extracting').hidden = true;
  document.getElementById('plan-empty').hidden = false;
}

function cancelConfirm() {
  if (waitingOn) deleteDocument(waitingOn.id);
  waitingOn = null;
  backToEmptyOrCurrent();
}

async function startExtraction() {
  if (!waitingOn) return;
  document.getElementById('plan-confirm').hidden = true;
  showExtracting(waitingOn.fileName);
  try {
    await enqueueDocument(waitingOn.id);
    pollUntilReady();
  } catch (err) {
    hideExtracting();
    // Still staged on the server: offer it again rather than strand it.
    showConfirm(waitingOn.fileName, waitingOn.pages);
    toast({ kind: 'error', title: 'Could not queue', msg: String(err.message || err) });
  }
}

// Poll, not stream: the run belongs to the server, and a poll has nothing
// to reconnect after a sleep or a dropped network (decision 8).
function pollUntilReady() {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!waitingOn || stopping) return;
    let doc;
    try {
      doc = await getDocument(waitingOn.id);
    } catch {
      pollUntilReady();
      return;
    }
    if (!waitingOn || stopping) return;   // Stop pressed while the request was out
    if (doc.state === 'queued') {
      document.getElementById('ex-detail').textContent =
        doc.queue_position > 1 ? `Waiting — ${doc.queue_position - 1} drawing(s) ahead` : 'Waiting for the worker…';
      pollUntilReady();
    } else if (doc.state === 'extracting') {
      if (doc.progress) onExtractProgress(doc.progress);
      pollUntilReady();
    } else {
      extractStepsDone();
      hideExtracting();
      routeDocument(doc);
    }
  }, 1500);
}

// Cancel is cooperative: the worker stops at its next progress report, and
// detection reports none while it runs -- so this can take many minutes,
// and the copy must not promise an immediate stop. The drawing is removed
// once the worker lets go (DELETE is refused until then); the interim UI
// has no list to come back to (B3 adds one).
async function stopExtraction() {
  if (!waitingOn || stopping) return;
  stopping = true;
  clearTimeout(pollTimer);
  const { id } = waitingOn;
  document.getElementById('ex-stop').disabled = true;
  document.getElementById('ex-title').textContent = 'Cancelling…';
  document.getElementById('ex-detail').textContent =
    'Stops at the next step — this can take a few minutes.';
  try { await cancelDocument(id); } catch { /* already finished or gone: remove it anyway */ }
  removeWhenStopped(id);
}

function removeWhenStopped(id) {
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!(await deleteDocument(id))) { removeWhenStopped(id); return; }
    stopping = false;
    waitingOn = null;
    document.getElementById('ex-stop').disabled = false;
    hideExtracting();
    backToEmptyOrCurrent();
    toast({ kind: 'ok', title: 'Stopped', msg: 'The drawing was removed.' });
  }, 1500);
}

function confirmLeaveUnsaved() {
  return confirm('Your latest changes to the open drawing are not saved yet — '
               + 'the server has not taken them.\n\n'
               + 'OK: open the other drawing anyway (those changes may be lost).\n'
               + 'Cancel: stay here; saving keeps retrying.');
}

async function loadForReview(id) {
  // journal.resume() discards the open drawing's unflushed events, so they
  // go to the server first -- or the reviewer decides, never silence.
  if (journal.active && journal.sessionId !== id) {
    if (!(await flushBeforeLeaving(journal, confirmLeaveUnsaved))) {
      waitingOn = null;
      hideExtracting();
      backToEmptyOrCurrent();
      toast({ kind: 'warn', title: 'Stayed on this drawing',
              msg: 'The other drawing is ready on the server; open its link once this one has saved.' });
      if (journal.pending.length) scheduleFlush(flushBackoff);
      return;
    }
    // Left anyway: one last best-effort try for whatever is still pending.
    if (journal.pending.length) beaconEvents(journal.sessionId, journal.writer, journal.pending);
  }
  setBusy('Opening drawing…');
  try {
    const data = await openDocument(id);
    // Restore, resume and seed with nothing in between: an op applied
    // between restoring and resuming would never be journaled. Throws with
    // the screen untouched or cleared, never half-built (reopen.js).
    restoreReview(data, { restoreSession, clearSession, journal, finisher });
    clearTimeout(flushTimer);
    flushBackoff = 500;
    waitingOn = null;
    setHash(id);
    hideExtracting();
    const n = data.rows.length;
    const nEv = data.events.length;
    toast({ kind: 'ok', title: `Loaded ${data.fileName}`,
            msg: nEv ? `Review restored (${nEv} change${nEv === 1 ? '' : 's'})`
                     : `${n} characteristic${n === 1 ? '' : 's'} extracted` });
  } catch (err) {
    waitingOn = null;
    hideExtracting();
    toast({ kind: 'error', title: 'Could not open the drawing',
            msg: `${String(err.message || err)} — reload the page to try again.` });
    if (state.sessionId) {
      backToEmptyOrCurrent();
    } else {
      // Keep the link on the drawing that failed, so a reload retries it.
      backToEmpty();
      setHash(id);
    }
  } finally {
    setIdle();
  }
}

// ===== Extraction status overlay ====================================
// Ordered pipeline steps, keyed to the `step` values the server emits.
const EXTRACT_STEPS = [
  { key: 'render', label: 'Rendering page' },
  { key: 'notes',  label: 'Reading notes block' },
  { key: 'title',  label: 'Reading title block' },
  { key: 'detect', label: 'Detecting characteristics' },
  { key: 'ocr',    label: 'Reading regions' },
  { key: 'place',  label: 'Placing balloons' },
];

function showExtracting(fileName) {
  document.getElementById('plan-empty').hidden = true;
  document.getElementById('ex-title').textContent = `Extracting ${fileName}`;
  document.getElementById('ex-detail').textContent = 'Starting…';

  const list = document.getElementById('ex-steps');
  list.innerHTML = '';
  for (const s of EXTRACT_STEPS) {
    const li = document.createElement('li');
    li.dataset.key = s.key;
    li.innerHTML =
      '<span class="ex-icon"><span class="ex-dot"></span>' +
      '<svg class="ex-check" width="14" height="14"><use href="#i-check"/></svg></span>' +
      `<span class="ex-label">${s.label}</span>`;
    list.appendChild(li);
  }
  document.getElementById('plan-extracting').hidden = false;
}

function hideExtracting() {
  document.getElementById('plan-extracting').hidden = true;
}

function onExtractProgress({ step, detail, current, total }) {
  const idx = EXTRACT_STEPS.findIndex((s) => s.key === step);
  if (idx === -1) return;

  const items = document.querySelectorAll('#ex-steps li');
  items.forEach((li, i) => {
    li.classList.toggle('done', i < idx);
    li.classList.toggle('active', i === idx);
  });

  let label = EXTRACT_STEPS[idx].label;
  if (total != null && current != null) label += ` · ${current}/${total}`;
  const active = items[idx];
  if (active) active.querySelector('.ex-label').textContent = label;
  document.getElementById('ex-detail').textContent = detail || label;
}

function extractStepsDone() {
  document.querySelectorAll('#ex-steps li').forEach((li) => {
    li.classList.remove('active');
    li.classList.add('done');
  });
  document.getElementById('ex-detail').textContent = 'Done';
}

function setBusy(label) {
  const pill = document.getElementById('status-pill');
  pill.hidden = false;
  pill.className = 'status-pill busy';
  document.getElementById('status-text').textContent = label;
}
function setIdle() {
  const pill = document.getElementById('status-pill');
  pill.hidden = true;
  pill.className = 'status-pill';
}

// ===== Journal =======================================================
function wireJournal() {
  on('op', (e) => {
    // A journal fault must never break the reviewer's edit: the state has
    // already changed and 'change' must still fire after this listener.
    // The error's TYPE only: its message could carry a cell value.
    try { journal.record(e); } catch (err) { console.error('review journal:', err?.name); }
    scheduleFlush(500);
  });
  // pagehide, not beforeunload: it also fires on mobile tab discards.
  window.addEventListener('pagehide', () => {
    if (journal.active) beaconEvents(journal.sessionId, journal.writer, journal.pending);
  });
}

function scheduleFlush(delay) {
  clearTimeout(flushTimer);
  flushTimer = setTimeout(async () => {
    try {
      await journal.flush();
      flushBackoff = 500;
    } catch (err) {
      // Another tab opened this drawing and holds the writer now; every
      // retry would be refused the same way (B3 replaces this with a
      // read-only banner).
      if (/opened elsewhere/.test(err?.message || '')) {
        toast({ kind: 'warn', title: 'Opened in another tab', msg: 'Reload to continue here.' });
        return;
      }
      // Keep everything pending and retry, backing off to 10 s; Finish
      // refuses until the server holds every event, so nothing is lost.
      flushBackoff = Math.min(flushBackoff * 2, 10000);
      scheduleFlush(flushBackoff);
    }
  }, delay);
}

// ===== Finish & Export ==============================================
function wireFinish() {
  const btn = document.getElementById('finish-btn');
  on('change', () => {
    if (!state.sessionId) return;
    const p = progress();
    btn.classList.toggle('blocked', !canFinish());
    btn.title = canFinish()
      ? 'Seal the review and export Excel + ballooned PDF'
      : `${p.outstanding} flagged row${p.outstanding === 1 ? '' : 's'} still to resolve`;
  });
  btn.addEventListener('click', finish);
}

async function finish() {
  const btn = document.getElementById('finish-btn');
  // A copy, so the sealed rows and the exported files describe one moment
  // even if an op lands while the run is awaiting the server.
  const snapshot = {
    sessionId: state.sessionId,
    rows: structuredClone(state.rows),
    reviewedIds: state.rows.filter((r) => r.reviewed).map((r) => r.id),
    notes: state.notes, marks: state.marks, title_block: state.title_block,
  };
  clearTimeout(flushTimer);   // the run flushes itself
  btn.disabled = true;        // no second click while sealing/exporting
  setBusy('Finishing…');
  try {
    const res = await finisher.run(snapshot);
    if (res.status === 'blocked') {
      const n = progress().outstanding;
      toast({ kind: 'warn', title: 'Not finished yet',
              msg: `${n} flagged row${n === 1 ? '' : 's'} still to resolve — showing them now.` });
      document.querySelector('#filter-pills button[data-filter="review"]')?.click();
    } else if (res.status === 'done') {
      toast({ kind: 'ok', title: 'Finished', msg: `Review sealed as revision r${res.revision}` });
    }
  } catch (err) {
    toast({ kind: 'error', title: 'Finish failed', msg: String(err.message || err) });
  } finally {
    setIdle();
    btn.disabled = !state.sessionId;
    // The flush timer was cleared above; if events are still pending (a
    // failed flush while offline, or a blocked run), restart the backoff loop
    // so they don't wait for the next edit.
    if (journal.active && journal.pending.length) scheduleFlush(flushBackoff);
  }
}

// ===== Undo / Redo ==================================================
function wireUndoRedo() {
  const u = document.getElementById('undo-btn');
  const r = document.getElementById('redo-btn');
  u.addEventListener('click', undo);
  r.addEventListener('click', redo);
  on('history', () => {
    u.disabled = !canUndo();
    r.disabled = !canRedo();
  });
}

// ===== Health =======================================================
async function pingHealth() {
  const pill  = document.getElementById('ocr-pill');
  const label = document.getElementById('ocr-label');
  const data  = await health();
  if (!data) {
    pill.classList.remove('ok'); pill.classList.add('warn');
    label.textContent = 'API unreachable';
    return;
  }
  pill.classList.add('ok');
  const backend = data.ocr_backend_active || data.backend || 'OCR';
  const short = String(backend).replace('Backend','');
  label.textContent = `OCR · ${short}`;
  pill.title = `OCR backend: ${backend}${data.cuda ? ' · CUDA' : ''}`;
}

document.addEventListener('DOMContentLoaded', init);
