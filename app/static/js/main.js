// Entry point: wires modules, health check, file upload, exports.

import { state, on, setSession, clearSession, undo, redo, canUndo, canRedo } from './state.js';
import { savePdf, runExtraction, deleteSession, exportFile, health } from './api.js';
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
  wireExports();
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
      document.getElementById('export-xlsx').disabled = false;
      document.getElementById('export-pdf').disabled = false;
    } else {
      chip.hidden = true;
      document.getElementById('export-xlsx').disabled = true;
      document.getElementById('export-pdf').disabled = true;
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

// ===== Exports ======================================================
function wireExports() {
  document.getElementById('export-xlsx').addEventListener('click', async () => {
    try {
      await exportFile('/api/export',
        { session_id: state.sessionId, rows: state.rows, notes: state.notes },
        'inspection.xlsx');
      toast({ kind: 'ok', title: 'Excel exported' });
    } catch (err) {
      toast({ kind: 'error', title: 'Export failed', msg: String(err.message || err) });
    }
  });
  document.getElementById('export-pdf').addEventListener('click', async () => {
    setBusy('Rendering ballooned PDF…');
    try {
      await exportFile('/api/export/pdf',
        { session_id: state.sessionId, rows: state.rows, notes: state.notes },
        'ballooned.pdf');
      setIdle();
      toast({ kind: 'ok', title: 'PDF exported' });
    } catch (err) {
      setIdle();
      toast({ kind: 'error', title: 'Export failed', msg: String(err.message || err) });
    }
  });
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
