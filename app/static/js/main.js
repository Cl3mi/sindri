// Entry point: wires modules, health check, file upload, exports.

import { state, on, setSession, clearSession, undo, redo, canUndo, canRedo,
         progress, canFinish } from './state.js';
import { savePdf, runExtraction, deleteSession, exportFile, health,
         postEvents, beaconEvents, sealSession } from './api.js';
import { createJournal } from './journal.js';
import { createFinisher } from './finish.js';
import { initViewer } from './viewer.js';
import { initTable } from './table.js';
import {
  toast, initModal, openHelp, initSplitter,
  initThemeAndDensity, initDragDrop,
} from './ui.js';
import { initShortcuts } from './shortcuts.js';

// Pending upload awaiting confirmation, and the controller for the live run.
let pendingUpload = null;   // { session_id, fileName }
let extractAbort = null;    // AbortController while extraction is streaming

// The review journal: every state operation, numbered and flushed to the
// server (docs/plans/2026-10-07-hitl-review-grading-design.md §2).
const journal = createJournal({
  send: (sid, writer, events) => postEvents(sid, writer, events),
});
let flushTimer = null;
let flushBackoff = 500;

// Flush → seal → export, single-flight, never re-sealing an unchanged
// journal (finish.js).
const finisher = createFinisher({
  journal,
  canFinish,
  seal: (sid, body) => sealSession(sid, body),
  exportAll: async (s) => {
    const payload = { session_id: s.sessionId, rows: s.rows.filter((r) => !r.suggested),
                      notes: s.notes, marks: s.marks, title_block: s.title_block };
    await exportFile('/api/export', payload, 'inspection.xlsx');
    await exportFile('/api/export/pdf', payload, 'ballooned.pdf');
  },
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
}

// ===== Header / file chip ============================================
function wireHeader() {
  document.getElementById('file-close').addEventListener('click', () => {
    if (!confirm('Close the current drawing? Unsaved edits will be lost.')) return;
    clearSession();
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
      document.getElementById('log-pill').hidden = true;
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
  if (extractAbort) {
    toast({ kind: 'warn', title: 'Extraction in progress', msg: 'Stop the current run before opening another drawing.' });
    return;
  }
  if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
    toast({ kind: 'warn', title: 'Unsupported file', msg: 'Please drop a .pdf' });
    return;
  }
  setBusy(`Opening ${file.name}…`);
  try {
    const meta = await savePdf(file);
    pendingUpload = { session_id: meta.session_id, fileName: meta.fileName || file.name };
    showConfirm(pendingUpload.fileName, meta.pages);
  } catch (err) {
    toast({ kind: 'error', title: 'Could not open PDF', msg: String(err.message || err) });
  } finally {
    setIdle();
  }
}

// ===== Confirm → Start → Stop ======================================
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
  if (pendingUpload) deleteSession(pendingUpload.session_id);
  pendingUpload = null;
  backToEmpty();
}

async function startExtraction() {
  if (!pendingUpload) return;
  const { session_id, fileName } = pendingUpload;
  document.getElementById('plan-confirm').hidden = true;
  showExtracting(fileName);
  setBusy(`Extracting from ${fileName}…`);
  extractAbort = new AbortController();
  try {
    const data = await runExtraction(session_id, onExtractProgress, extractAbort.signal);
    data.fileName = fileName;
    extractStepsDone();
    setSession(data);          // viewer swaps in the page image, hides overlays
    // Nothing may run between these two lines: setSession clears the
    // undo/redo stacks, so no op can be applied before the journal starts --
    // an op applied in between would never be logged, and seal would then
    // report a replay mismatch for a perfectly healthy session.
    journal.start(data.session_id, data.writer);
    document.getElementById('log-pill').hidden = !!data.review_logging;
    hideExtracting();
    setIdle();
    pendingUpload = null;
    extractAbort = null;
    const charsN = data.rows.length;
    toast({
      kind: 'ok',
      title: `Loaded ${fileName}`,
      msg: `${charsN} characteristic${charsN === 1 ? '' : 's'} extracted`,
    });
  } catch (err) {
    hideExtracting();
    setIdle();
    extractAbort = null;
    if (err.name === 'AbortError') {   // user pressed Stop — session already cleaned up
      backToEmpty();
      return;
    }
    deleteSession(session_id);
    pendingUpload = null;
    backToEmpty();
    toast({ kind: 'error', title: 'Could not extract', msg: String(err.message || err) });
  }
}

function stopExtraction() {
  if (extractAbort) extractAbort.abort();   // rejects runExtraction with AbortError
  if (pendingUpload) deleteSession(pendingUpload.session_id);
  pendingUpload = null;
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
    try { journal.record(e); } catch (err) { console.error('review journal', err); }
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
    } catch {
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
      toast({ kind: 'ok', title: 'Finished',
              msg: res.revision ? `Review sealed as revision r${res.revision}` : 'Exported (review not recorded)' });
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
