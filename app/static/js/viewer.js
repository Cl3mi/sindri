// Plan viewer: transform-based zoom/pan + marker overlay.
//
// Coordinates:
//   plan-space      = pixels in the source PNG (img.naturalWidth × naturalHeight)
//   viewport-space  = pixels inside #plan-viewport (after transform)
//
//   viewport.x = plan.x * zoom + tx
//   viewport.y = plan.y * zoom + ty
//
// Markers are positioned in viewport-space each frame (so they keep a constant
// size as the user zooms).  The image lives inside #plan-stage which IS
// transformed.

import {
  state, on, emit,
  apply, opAddRow, opDeleteRow, opMoveRow,
} from './state.js';
import { readRegion } from './api.js';
import { toast } from './ui.js';

const ZOOM_MIN = 0.1;
const ZOOM_MAX = 8;
const ZOOM_STEP = 1.2;

let viewport, stage, markerLayer, rubber, img, empty;
let zoom = 1, tx = 0, ty = 0;
let panning = false, spaceDown = false, panStart = null;
let addMode = false;
let drawing = null;        // { startView }
let dragging = null;       // { id, lastView }

export function initViewer() {
  viewport     = document.getElementById('plan-viewport');
  stage        = document.getElementById('plan-stage');
  markerLayer  = document.getElementById('marker-layer');
  rubber       = document.getElementById('rubber-band');
  empty        = document.getElementById('plan-empty');

  on('session', onSession);
  on('change', renderMarkers);

  bindZoomControls();
  bindMouse();
  bindKeys();
  bindResize();
}

function bindZoomControls() {
  document.getElementById('zoom-in') .addEventListener('click', () => zoomBy(ZOOM_STEP, viewportCenter()));
  document.getElementById('zoom-out').addEventListener('click', () => zoomBy(1 / ZOOM_STEP, viewportCenter()));
  document.getElementById('fit-width').addEventListener('click', fitWidth);
  document.getElementById('fit-page') .addEventListener('click', fitPage);
  document.getElementById('zoom-100') .addEventListener('click', () => setZoom(1, viewportCenter()));
  document.getElementById('add-btn')  .addEventListener('click', toggleAdd);
}

function onSession() {
  // (re)attach image
  stage.innerHTML = '';
  if (!state.imageUrl) {
    empty.hidden = false;
    renderMarkers();
    return;
  }
  empty.hidden = true;
  img = document.createElement('img');
  img.draggable = false;
  img.src = state.imageUrl + '?t=' + Date.now();
  img.onload = () => {
    state.imageSize = { w: img.naturalWidth, h: img.naturalHeight };
    fitPage();
    emit('change');     // markers
  };
  stage.appendChild(img);
}

function bindResize() {
  const ro = new ResizeObserver(() => {
    // re-apply transform so we don't drift off-screen on resize
    applyTransform();
    renderMarkers();
  });
  ro.observe(viewport);
}

// ----- transform math --------------------------------------------------
function applyTransform(animate = false) {
  stage.classList.toggle('no-anim', !animate);
  stage.style.transform = `translate(${tx}px, ${ty}px) scale(${zoom})`;
  updateZoomReadout();
}
function updateZoomReadout() {
  const el = document.getElementById('zoom-readout');
  if (el) el.textContent = state.imageUrl ? `${Math.round(zoom * 100)}%` : '—';
}
function viewportCenter() {
  const r = viewport.getBoundingClientRect();
  return { x: r.width / 2, y: r.height / 2 };
}
function viewportPoint(clientX, clientY) {
  const r = viewport.getBoundingClientRect();
  return { x: clientX - r.left, y: clientY - r.top };
}
function planToView(px, py) {
  return { x: px * zoom + tx, y: py * zoom + ty };
}
function viewToPlan(vx, vy) {
  return { x: (vx - tx) / zoom, y: (vy - ty) / zoom };
}
function setZoom(newZoom, anchorVP) {
  newZoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, newZoom));
  const a = anchorVP || viewportCenter();
  const plan = viewToPlan(a.x, a.y);
  zoom = newZoom;
  tx = a.x - plan.x * zoom;
  ty = a.y - plan.y * zoom;
  applyTransform();
  renderMarkers();
}
function zoomBy(factor, anchorVP) { setZoom(zoom * factor, anchorVP); }

function fitWidth() {
  if (!img) return;
  const r = viewport.getBoundingClientRect();
  const z = r.width / img.naturalWidth;
  setZoom(z, { x: 0, y: 0 });
  tx = 0; ty = 0;
  applyTransform(true);
  renderMarkers();
}
function fitPage() {
  if (!img) return;
  const r = viewport.getBoundingClientRect();
  const z = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight) * 0.98;
  zoom = z;
  tx = (r.width  - img.naturalWidth  * z) / 2;
  ty = (r.height - img.naturalHeight * z) / 2;
  applyTransform(true);
  renderMarkers();
}

// ----- mouse + wheel --------------------------------------------------
function bindMouse() {
  viewport.addEventListener('wheel', (e) => {
    if (!img) return;
    e.preventDefault();
    const f = Math.pow(1.0015, -e.deltaY);
    zoomBy(f, viewportPoint(e.clientX, e.clientY));
  }, { passive: false });

  viewport.addEventListener('mousedown', (e) => {
    if (!img) return;
    if (e.target.closest('.marker .del')) return;        // delete-x click
    const onMarker = e.target.closest('.marker');

    // PAN (space+drag or middle-button)
    if ((spaceDown && e.button === 0) || e.button === 1) {
      e.preventDefault();
      panning = true;
      panStart = { x: e.clientX, y: e.clientY, tx, ty };
      viewport.classList.add('panning');
      return;
    }

    // ADD: rubber-band on background
    if (addMode && !onMarker && e.button === 0) {
      const v = viewportPoint(e.clientX, e.clientY);
      drawing = { startView: v };
      rubber.style.display = 'block';
      Object.assign(rubber.style, { left: v.x + 'px', top: v.y + 'px', width: '0px', height: '0px' });
      return;
    }

    // MARKER drag
    if (onMarker && e.button === 0) {
      const id = onMarker.dataset.id;
      const v = viewportPoint(e.clientX, e.clientY);
      dragging = { id, lastView: v, moved: false };
      state.selectedId = id;
      onMarker.classList.add('selected');
      emit('select', id);
      e.preventDefault();
    }
  });

  window.addEventListener('mousemove', (e) => {
    if (panning) {
      tx = panStart.tx + (e.clientX - panStart.x);
      ty = panStart.ty + (e.clientY - panStart.y);
      applyTransform();
      renderMarkers();
      return;
    }
    if (drawing) {
      const v = viewportPoint(e.clientX, e.clientY);
      const x = Math.min(drawing.startView.x, v.x);
      const y = Math.min(drawing.startView.y, v.y);
      Object.assign(rubber.style, {
        left:   x + 'px',
        top:    y + 'px',
        width:  Math.abs(v.x - drawing.startView.x) + 'px',
        height: Math.abs(v.y - drawing.startView.y) + 'px',
      });
      return;
    }
    if (dragging) {
      const v = viewportPoint(e.clientX, e.clientY);
      dragging.moved = true;
      // live update the marker DOM (commit to state on mouseup)
      const m = markerLayer.querySelector(`.marker[data-id="${cssEsc(dragging.id)}"]`);
      if (m) {
        m.style.left = v.x + 'px';
        m.style.top  = v.y + 'px';
      }
      dragging.lastView = v;
    }
  });

  window.addEventListener('mouseup', async (e) => {
    if (panning) {
      panning = false;
      viewport.classList.remove('panning');
    }
    if (drawing) {
      const start = drawing.startView;
      const v = viewportPoint(e.clientX, e.clientY);
      const p0 = viewToPlan(Math.min(start.x, v.x), Math.min(start.y, v.y));
      const p1 = viewToPlan(Math.max(start.x, v.x), Math.max(start.y, v.y));
      rubber.style.display = 'none';
      drawing = null;
      if ((p1.x - p0.x) >= 4 && (p1.y - p0.y) >= 4) {
        await runReadRegion([p0.x, p0.y, p1.x, p1.y]);
      }
    }
    if (dragging) {
      if (dragging.moved) {
        const planXY = viewToPlan(dragging.lastView.x, dragging.lastView.y);
        apply(opMoveRow(dragging.id, [planXY.x, planXY.y]));
      }
      dragging = null;
    }
  });

  // hover linkage
  markerLayer.addEventListener('mouseover', (e) => {
    const m = e.target.closest('.marker');
    if (!m) return;
    state.hoverId = m.dataset.id;
    emit('hover', state.hoverId);
  });
  markerLayer.addEventListener('mouseout', (e) => {
    const m = e.target.closest('.marker');
    if (!m) return;
    state.hoverId = null;
    emit('hover', null);
  });

  // delete-x click
  markerLayer.addEventListener('click', (e) => {
    const del = e.target.closest('.del');
    if (del) {
      const m = del.closest('.marker');
      const id = m?.dataset.id;
      if (id) apply(opDeleteRow(id));
      e.stopPropagation();
      return;
    }
    const m = e.target.closest('.marker');
    if (m) {
      state.selectedId = m.dataset.id;
      emit('select', state.selectedId);
      emit('change');
    }
  });
}

function bindKeys() {
  window.addEventListener('keydown', (e) => {
    if (isEditingText(e.target)) return;
    if (e.key === ' ' && !spaceDown) {
      spaceDown = true; viewport.classList.add('space-down');
      e.preventDefault();
    }
    if (e.key === '+' || e.key === '=') { zoomBy(ZOOM_STEP, viewportCenter()); e.preventDefault(); }
    if (e.key === '-' || e.key === '_') { zoomBy(1 / ZOOM_STEP, viewportCenter()); e.preventDefault(); }
    if (e.key === '0') { setZoom(1, viewportCenter()); }
    if (e.key.toLowerCase() === 'w') { fitWidth(); }
    if (e.key.toLowerCase() === 'f') { fitPage(); }
    if (e.key.toLowerCase() === 'a') { toggleAdd(); }
    if (e.key === 'Delete' || e.key === 'Backspace') {
      if (state.selectedId) { apply(opDeleteRow(state.selectedId)); e.preventDefault(); }
    }
    // nudge selected
    if (state.selectedId && ['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key)) {
      e.preventDefault();
      const step = (e.shiftKey ? 10 : 1) / zoom;     // step in plan pixels
      const row = state.rows.find((r) => r.id === state.selectedId);
      if (!row || !row.balloon_xy) return;
      const [x, y] = row.balloon_xy;
      const nx = x + (e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0);
      const ny = y + (e.key === 'ArrowUp'   ? -step : e.key === 'ArrowDown'  ? step : 0);
      apply(opMoveRow(state.selectedId, [nx, ny]));
    }
  });
  window.addEventListener('keyup', (e) => {
    if (e.key === ' ') { spaceDown = false; viewport.classList.remove('space-down'); }
  });
}
function isEditingText(el) {
  if (!el) return false;
  if (el.isContentEditable) return true;
  const t = el.tagName;
  return t === 'INPUT' || t === 'TEXTAREA' || t === 'SELECT';
}

function toggleAdd() {
  addMode = !addMode;
  document.getElementById('add-btn').classList.toggle('active', addMode);
  viewport.classList.toggle('adding', addMode);
}

async function runReadRegion(planBox) {
  try {
    const row = await readRegion(state.sessionId, planBox);
    row.reviewed = false;
    apply(opAddRow(row));
    toast({ kind: 'ok', title: `Added Pos ${row.pos}`, msg: row.char_type || row.raw_text || 'manual region' });
  } catch (err) {
    toast({ kind: 'error', title: 'Could not read region', msg: String(err.message || err) });
  }
}

// ----- marker rendering ----------------------------------------------
function cssEsc(id) {
  return (window.CSS && CSS.escape) ? CSS.escape(id) : String(id).replace(/"/g, '\\"');
}

function renderMarkers() {
  if (!img) { markerLayer.innerHTML = ''; return; }
  const existing = new Map();
  markerLayer.querySelectorAll('.marker').forEach((m) => existing.set(m.dataset.id, m));

  const seen = new Set();
  for (const r of state.rows) {
    if (!r.balloon_xy) continue;
    seen.add(r.id);
    const v = planToView(r.balloon_xy[0], r.balloon_xy[1]);
    let m = existing.get(r.id);
    if (!m) {
      m = document.createElement('div');
      m.className = 'marker';
      m.dataset.id = r.id;
      const del = document.createElement('div');
      del.className = 'del'; del.textContent = '×';
      m.appendChild(del);
      const label = document.createElement('span');
      label.className = 'label';
      m.appendChild(label);
      markerLayer.appendChild(m);
    }
    m.style.left = v.x + 'px';
    m.style.top  = v.y + 'px';
    const label = m.querySelector('.label');
    if (label && label.textContent !== String(r.pos)) label.textContent = r.pos;
    m.classList.toggle('review',       r.needs_review && !r.reviewed);
    m.classList.toggle('has-noteref',  !!r.note_ref_pos);
    m.classList.toggle('selected',     r.id === state.selectedId);
    m.classList.toggle('linked',       r.id === state.hoverId && r.id !== state.selectedId);
    m.title = `Pos ${r.pos}` + (r.review_reasons?.length ? ' · ' + r.review_reasons.join(', ') : '');
  }
  // remove markers that no longer exist
  for (const [id, el] of existing) {
    if (!seen.has(id)) el.remove();
  }
}

// ----- exported helpers used by table.js ------------------------------
export function flashAndCenter(id) {
  const row = state.rows.find((r) => r.id === id);
  if (!row || !row.balloon_xy) return;
  const r = viewport.getBoundingClientRect();
  // place this plan-point at viewport center
  const cx = r.width / 2, cy = r.height / 2;
  tx = cx - row.balloon_xy[0] * zoom;
  ty = cy - row.balloon_xy[1] * zoom;
  applyTransform(true);
  renderMarkers();
  const m = markerLayer.querySelector(`.marker[data-id="${cssEsc(id)}"]`);
  if (m) {
    m.classList.remove('flash');
    void m.offsetWidth;                  // restart animation
    m.classList.add('flash');
  }
}
