// Entry point: wires modules, health check, file upload, exports.

import { state, on, setSession, clearSession, undo, redo, canUndo, canRedo } from './state.js';
import { uploadPdf, exportFile, health } from './api.js';
import { initViewer } from './viewer.js';
import { initTable } from './table.js';
import {
  toast, initModal, openHelp, initSplitter,
  initThemeAndDensity, initDragDrop,
} from './ui.js';
import { initShortcuts } from './shortcuts.js';

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
  setBusy(`Extracting from ${file.name}…`);
  try {
    const data = await uploadPdf(file);
    data.fileName = file.name;
    setSession(data);
    setIdle();
    const charsN = data.rows.length;
    toast({
      kind: 'ok',
      title: `Loaded ${file.name}`,
      msg: `${charsN} characteristic${charsN === 1 ? '' : 's'} extracted`,
    });
  } catch (err) {
    setIdle();
    toast({ kind: 'error', title: 'Could not extract', msg: String(err.message || err) });
  }
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
