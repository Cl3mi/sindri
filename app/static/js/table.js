// Inspection table: rendering, edit, filter, search, bulk-accept.
// Hover and selection are bidirectional with the plan viewer via state events.

import {
  state, on, emit,
  apply, opEditCell, opBulkReview, isVisibleRow, counts,
} from './state.js';
import { flashAndCenter } from './viewer.js';

const COLS = ['char_type', 'nominal', 'upper_tol', 'lower_tol'];
const NUMERIC = new Set(['nominal', 'upper_tol', 'lower_tol']);

let body, notesBody, notesSection, notesCount;
let marksBody, marksSection, marksCount;
let titleBody, titleSection, titleCount;

export function initTable() {
  body         = document.getElementById('grid-body');
  notesBody    = document.getElementById('notes-body');
  notesSection = document.getElementById('notes-section');
  notesCount   = document.getElementById('notes-count');
  marksBody    = document.getElementById('marks-body');
  marksSection = document.getElementById('marks-section');
  marksCount   = document.getElementById('marks-count');
  titleBody    = document.getElementById('title-body');
  titleSection = document.getElementById('title-section');
  titleCount   = document.getElementById('title-count');

  on('session', renderAll);
  on('change',  renderAll);
  on('hover',   syncHoverFromViewer);
  on('select',  syncSelectFromViewer);

  bindFilters();
  bindSearch();
  bindSelectAll();
  bindBulkAccept();
  bindNotesToggle();
  bindMarksToggle();
  bindTitleToggle();
}

function bindFilters() {
  document.querySelectorAll('#filter-pills button').forEach((btn) => {
    btn.addEventListener('click', () => {
      state.filter = btn.dataset.filter;
      document.querySelectorAll('#filter-pills button').forEach((b) =>
        b.classList.toggle('active', b === btn));
      renderRows();
      updateBulkAvailability();
    });
  });
}

function bindSearch() {
  const wrap  = document.getElementById('search-wrap');
  const input = document.getElementById('search-input');
  const clear = document.getElementById('search-clear');
  input.addEventListener('input', () => {
    state.search = input.value;
    wrap.classList.toggle('has-value', !!input.value);
    renderRows();
  });
  clear.addEventListener('click', () => {
    input.value = '';
    state.search = '';
    wrap.classList.remove('has-value');
    renderRows();
    input.focus();
  });
}

function bindSelectAll() {
  const all = document.getElementById('select-all');
  all.addEventListener('change', () => {
    body.querySelectorAll('tr:not(.hidden) .row-check').forEach((cb) => {
      cb.checked = all.checked;
    });
    updateBulkAvailability();
  });
}

function bindBulkAccept() {
  document.getElementById('bulk-accept').addEventListener('click', () => {
    const selectedIds = [...body.querySelectorAll('.row-check:checked')].map((cb) => cb.dataset.id);
    const targetIds = selectedIds.length > 0
      ? selectedIds
      : state.rows.filter(isVisibleRow).filter((r) => !r.reviewed).map((r) => r.id);
    if (targetIds.length === 0) return;
    apply(opBulkReview(targetIds, true));
  });
}

function bindNotesToggle() {
  document.getElementById('notes-toggle').addEventListener('click', () => {
    const collapsed = notesSection.dataset.collapsed === 'true';
    notesSection.dataset.collapsed = collapsed ? 'false' : 'true';
  });
}

function bindMarksToggle() {
  document.getElementById('marks-toggle').addEventListener('click', () => {
    const collapsed = marksSection.dataset.collapsed === 'true';
    marksSection.dataset.collapsed = collapsed ? 'false' : 'true';
  });
}

function bindTitleToggle() {
  document.getElementById('title-toggle').addEventListener('click', () => {
    const collapsed = titleSection.dataset.collapsed === 'true';
    titleSection.dataset.collapsed = collapsed ? 'false' : 'true';
  });
}

// ===== Rendering ====================================================
function renderAll() {
  renderRows();
  renderNotes();
  renderMarks();
  renderTitleBlock();
  renderCounts();
  updateBulkAvailability();
}

function renderRows() {
  body.innerHTML = '';
  for (const r of state.rows) {
    const tr = document.createElement('tr');
    tr.dataset.id = r.id;
    if (r.needs_review && !r.reviewed) tr.classList.add('review');
    if (r.reviewed) tr.classList.add('reviewed');
    if (r.id === state.selectedId) tr.classList.add('selected');
    if (!isVisibleRow(r)) tr.classList.add('hidden');
    if (r.review_reasons?.length) tr.title = r.review_reasons.join(', ');

    // checkbox
    const tdC = document.createElement('td');
    tdC.className = 'check';
    const cb = document.createElement('input');
    cb.type = 'checkbox'; cb.className = 'checkbox row-check';
    cb.dataset.id = r.id;
    cb.addEventListener('change', updateBulkAvailability);
    tdC.appendChild(cb);
    tr.appendChild(tdC);

    // pos
    const tdP = document.createElement('td');
    tdP.className = 'pos';
    const dot = document.createElement('span'); dot.className = 'review-dot';
    if (!(r.needs_review && !r.reviewed) && !r.reviewed) dot.style.visibility = 'hidden';
    const num = document.createElement('span'); num.className = 'pos-num'; num.textContent = r.pos;
    tdP.appendChild(dot);
    tdP.appendChild(num);
    if (r.note_ref_pos) {
      const ref = document.createElement('span');
      ref.className = 'note-ref'; ref.dataset.pos = r.note_ref_pos;
      ref.title = 'Jump to referenced note';
      ref.textContent = '→' + r.note_ref_pos;
      tdP.appendChild(ref);
    }
    tdP.addEventListener('click', () => flashAndCenter(r.id));
    tdP.style.cursor = 'pointer';
    tr.appendChild(tdP);

    // editable cells
    for (const k of COLS) {
      const td = document.createElement('td');
      td.contentEditable = 'true';
      td.spellcheck = false;
      td.dataset.id = r.id;
      td.dataset.field = k;
      if (NUMERIC.has(k)) td.className = 'num mono';
      else if (k === 'char_type') td.className = 'mono';
      td.textContent = r[k] ?? '';

      td.addEventListener('focus', () => { td.dataset._orig = td.textContent; });
      td.addEventListener('blur', () => {
        const newVal = td.textContent.trim();
        if (newVal === td.dataset._orig) return;
        apply(opEditCell(r.id, k, newVal));
      });
      td.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); td.blur(); }
        if (e.key === 'Escape') { td.textContent = td.dataset._orig ?? ''; td.blur(); }
      });
      td.addEventListener('paste', (e) => {        // strip HTML on paste
        e.preventDefault();
        const text = (e.clipboardData || window.clipboardData).getData('text/plain');
        document.execCommand('insertText', false, text);
      });
      tr.appendChild(td);
    }

    tr.addEventListener('mouseenter', () => {
      state.hoverId = r.id; emit('hover', r.id);
    });
    tr.addEventListener('mouseleave', () => {
      state.hoverId = null; emit('hover', null);
    });

    // jump to balloon on note-ref click
    const ref = tr.querySelector('.note-ref');
    if (ref) ref.addEventListener('click', (e) => {
      e.stopPropagation();
      const targetPos = +ref.dataset.pos;
      const target = document.getElementById(`note-${targetPos}`);
      if (target) {
        target.classList.add('target');
        target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(() => target.classList.remove('target'), 1100);
      }
    });

    body.appendChild(tr);
  }
}

function renderNotes() {
  notesBody.innerHTML = '';
  const block = state.notes;
  if (!block || !block.notes || block.notes.length === 0) {
    notesSection.hidden = true;
    return;
  }
  notesSection.hidden = false;
  notesCount.textContent = block.notes.length;
  for (const n of block.notes) {
    const tr = document.createElement('tr');
    const isSub = n.parent_pos != null;
    if (n.needs_review) {
      tr.classList.add('review');
      tr.title = (n.review_reasons || []).join(', ');
    }
    const posLabel = isSub ? `${n.parent_pos}.${n.sub_index}` : `${n.pos}`;
    const posTd = document.createElement('td');
    posTd.className = 'pos' + (isSub ? ' sub' : '');
    posTd.textContent = posLabel;
    if (!isSub) posTd.id = `note-${n.pos}`;
    tr.appendChild(posTd);
    const en = document.createElement('td'); en.textContent = n.text_en ?? ''; tr.appendChild(en);
    const de = document.createElement('td'); de.textContent = n.text_de ?? ''; tr.appendChild(de);
    notesBody.appendChild(tr);
  }
}

function renderMarks() {
  marksBody.innerHTML = '';
  const block = state.marks;
  if (!block || !block.marks || block.marks.length === 0) {
    marksSection.hidden = true;
    return;
  }
  marksSection.hidden = false;
  marksCount.textContent = block.marks.length;
  for (const m of block.marks) {
    const tr = document.createElement('tr');
    if (m.needs_review) {
      tr.classList.add('review');
      tr.title = (m.review_reasons || []).join(', ');
    }
    const posTd = document.createElement('td');
    posTd.className = 'pos';
    posTd.textContent = `${m.pos}`;
    posTd.id = `mark-${m.pos}`;
    tr.appendChild(posTd);
    const en = document.createElement('td'); en.textContent = m.text_en ?? ''; tr.appendChild(en);
    const de = document.createElement('td'); de.textContent = m.text_de ?? ''; tr.appendChild(de);
    marksBody.appendChild(tr);
  }
}

function renderTitleBlock() {
  titleBody.innerHTML = '';
  const fields = state.title_block;
  if (!fields || fields.length === 0) {
    titleSection.hidden = true;
    return;
  }
  titleSection.hidden = false;
  titleCount.textContent = fields.length;
  for (const f of fields) {
    const tr = document.createElement('tr');
    if (f.needs_review) {
      tr.classList.add('review');
      tr.title = (f.review_reasons || []).join(', ');
    }
    const en = document.createElement('td'); en.textContent = f.label_en ?? ''; tr.appendChild(en);
    const de = document.createElement('td'); de.textContent = f.label_de ?? ''; tr.appendChild(de);
    const val = document.createElement('td'); val.textContent = f.value ?? ''; tr.appendChild(val);
    titleBody.appendChild(tr);
  }
}

function renderCounts() {
  const c = counts();
  document.getElementById('cnt-all').textContent    = c.all;
  document.getElementById('cnt-review').textContent = c.review;
  document.getElementById('cnt-ok').textContent     = c.ok;

  const reviewedN = state.rows.filter((r) => r.reviewed || !r.needs_review).length;
  const totalN = state.rows.length;
  const prog = document.getElementById('review-progress');
  if (totalN > 0) {
    prog.hidden = false;
    document.getElementById('review-text').textContent = `${reviewedN}/${totalN} reviewed`;
    document.getElementById('review-bar').style.width = (totalN ? (reviewedN / totalN * 100) : 0) + '%';
  } else {
    prog.hidden = true;
  }

  // Footer counts
  const totalSeg  = document.getElementById('foot-total');
  const totalText = document.getElementById('foot-total-text');
  const revSeg    = document.getElementById('foot-review');
  const revText   = document.getElementById('foot-review-text');
  if (state.sessionId) {
    totalText.textContent = `${totalN} characteristics`;
    totalSeg.classList.add('ok');
    if (c.review > 0) {
      revSeg.hidden = false;
      revText.textContent = `${c.review} to review`;
    } else { revSeg.hidden = true; }
  } else {
    totalText.textContent = 'no session';
    totalSeg.classList.remove('ok');
    revSeg.hidden = true;
  }

  const selSeg  = document.getElementById('foot-selected');
  const selText = document.getElementById('foot-selected-text');
  if (state.selectedId) {
    const r = state.rows.find((x) => x.id === state.selectedId);
    if (r) {
      selSeg.hidden = false;
      selText.textContent = `Pos ${r.pos} selected`;
    }
  } else {
    selSeg.hidden = true;
  }

  // session id chip
  const sessSeg = document.getElementById('foot-session');
  if (state.sessionId) {
    sessSeg.hidden = false;
    sessSeg.textContent = 'session ' + state.sessionId.slice(0, 8) + '…';
  } else {
    sessSeg.hidden = true;
  }
}

function updateBulkAvailability() {
  const checked = body.querySelectorAll('.row-check:checked').length;
  const visibleUnreviewed = state.rows.filter(isVisibleRow).filter((r) => !r.reviewed).length;
  const btn = document.getElementById('bulk-accept');
  btn.disabled = checked === 0 && visibleUnreviewed === 0;
  const label = document.getElementById('bulk-accept-label');
  if (label) label.textContent = checked > 0 ? `Accept ${checked}` : 'Accept all';
}

// ===== sync from viewer =============================================
function syncHoverFromViewer(id) {
  body.querySelectorAll('tr.linked').forEach((tr) => tr.classList.remove('linked'));
  if (!id) return;
  const tr = body.querySelector(`tr[data-id="${cssEsc(id)}"]`);
  if (tr) {
    tr.classList.add('linked');
    // scroll into view if off-screen
    const wrap = document.getElementById('table-wrap');
    const wb = wrap.getBoundingClientRect();
    const rb = tr.getBoundingClientRect();
    if (rb.top < wb.top || rb.bottom > wb.bottom) {
      tr.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }
}
function syncSelectFromViewer(id) {
  body.querySelectorAll('tr.selected').forEach((tr) => tr.classList.remove('selected'));
  if (!id) return;
  const tr = body.querySelector(`tr[data-id="${cssEsc(id)}"]`);
  if (tr) tr.classList.add('selected');
  renderCounts();
}

function cssEsc(id) {
  return (window.CSS && CSS.escape) ? CSS.escape(id) : String(id).replace(/"/g, '\\"');
}
