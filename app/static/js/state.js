// Single source of truth for session state + undo/redo + event bus.
//
// Components should mutate state ONLY via the apply()/operation system or via
// the direct setters defined here, so undo/redo stays consistent.  Subscribers
// receive a `change` event after each apply() and react idempotently.

const listeners = new Map();   // event -> Set<fn>

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event)?.delete(fn);
}
export function emit(event, payload) {
  listeners.get(event)?.forEach((fn) => fn(payload));
}

export const state = {
  sessionId: null,
  imageUrl: null,
  imageSize: { w: 0, h: 0 },
  fileName: null,
  rows: [],          // each row gets a transient `reviewed: bool` client-side
  notes: null,
  selectedId: null,  // selected marker / row id
  hoverId: null,
  filter: 'all',     // 'all' | 'review' | 'ok'
  search: '',
  ocrBackend: '—',
  ocrOk: null,       // null | true | false
};

export function setSession(payload) {
  state.sessionId = payload.session_id;
  state.imageUrl  = payload.image_url;
  state.rows      = payload.rows.map((r) => ({ ...r, reviewed: false }));
  state.notes     = payload.notes;
  state.fileName  = payload.fileName ?? state.fileName;
  state.selectedId = null;
  undoStack.length = 0;
  redoStack.length = 0;
  emit('session', state);
  emit('change');
}

export function clearSession() {
  state.sessionId = null;
  state.imageUrl  = null;
  state.imageSize = { w: 0, h: 0 };
  state.fileName  = null;
  state.rows      = [];
  state.notes     = null;
  state.selectedId = null;
  undoStack.length = 0;
  redoStack.length = 0;
  emit('session', state);
  emit('change');
}

// ===== Undo / redo ====================================================
const undoStack = [];
const redoStack = [];

export function apply(op) {
  op.do();
  undoStack.push(op);
  redoStack.length = 0;
  emit('change');
  emit('history');
}
export function undo() {
  const op = undoStack.pop();
  if (!op) return;
  op.undo();
  redoStack.push(op);
  emit('change');
  emit('history');
}
export function redo() {
  const op = redoStack.pop();
  if (!op) return;
  op.do();
  undoStack.push(op);
  emit('change');
  emit('history');
}
export const canUndo = () => undoStack.length > 0;
export const canRedo = () => redoStack.length > 0;

// ===== Reading-order renumber (matches the original logic) ============
const BAND_TOL = 60;
export function renumber() {
  const c = (r) => r.target_region
    ? [(r.target_region[1] + r.target_region[3]) / 2,
       (r.target_region[0] + r.target_region[2]) / 2]
    : (r.balloon_xy ? [r.balloon_xy[1], r.balloon_xy[0]] : [0, 0]);
  state.rows.sort((a, b) => {
    const [ay, ax] = c(a), [by, bx] = c(b);
    const band = Math.round(ay / BAND_TOL) - Math.round(by / BAND_TOL);
    return band !== 0 ? band : ax - bx;
  });
  state.rows.forEach((r, i) => (r.pos = i + 1));
}

// ===== Operations =====================================================
export function opAddRow(row) {
  const snapshot = { row };
  return {
    label: 'add balloon',
    do() {
      state.rows.push(row);
      renumber();
      state.selectedId = row.id;
    },
    undo() {
      state.rows = state.rows.filter((r) => r.id !== row.id);
      renumber();
      if (state.selectedId === row.id) state.selectedId = null;
    },
  };
}

export function opDeleteRow(id) {
  let removed = null;
  return {
    label: 'delete balloon',
    do() {
      removed = state.rows.find((r) => r.id === id) || null;
      state.rows = state.rows.filter((r) => r.id !== id);
      renumber();
      if (state.selectedId === id) state.selectedId = null;
    },
    undo() {
      if (removed) {
        state.rows.push(removed);
        renumber();
      }
    },
  };
}

export function opMoveRow(id, newXY) {
  let oldXY = null;
  return {
    label: 'move balloon',
    do() {
      const r = state.rows.find((x) => x.id === id);
      if (!r) return;
      oldXY = r.balloon_xy ? [...r.balloon_xy] : null;
      r.balloon_xy = [...newXY];
    },
    undo() {
      const r = state.rows.find((x) => x.id === id);
      if (r) r.balloon_xy = oldXY;
    },
  };
}

export function opEditCell(id, field, newValue) {
  let oldValue = null;
  return {
    label: `edit ${field}`,
    do() {
      const r = state.rows.find((x) => x.id === id);
      if (!r) return;
      oldValue = r[field];
      r[field] = newValue;
    },
    undo() {
      const r = state.rows.find((x) => x.id === id);
      if (r) r[field] = oldValue;
    },
  };
}

export function opBulkReview(ids, target /* true|false */) {
  const prev = new Map();
  return {
    label: target ? 'accept rows' : 'unaccept rows',
    do() {
      for (const id of ids) {
        const r = state.rows.find((x) => x.id === id);
        if (r) { prev.set(id, r.reviewed); r.reviewed = target; }
      }
    },
    undo() {
      for (const id of ids) {
        const r = state.rows.find((x) => x.id === id);
        if (r) r.reviewed = prev.get(id);
      }
    },
  };
}

// ===== Filtering / counts ============================================
export function isVisibleRow(r) {
  if (state.filter === 'review' && !(r.needs_review && !r.reviewed)) return false;
  if (state.filter === 'ok'     &&  (r.needs_review && !r.reviewed)) return false;
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  const hay = `${r.pos} ${r.char_type} ${r.nominal} ${r.upper_tol} ${r.lower_tol}`.toLowerCase();
  return hay.includes(q);
}
export function counts() {
  let review = 0, ok = 0;
  for (const r of state.rows) {
    if (r.needs_review && !r.reviewed) review++; else ok++;
  }
  return { all: state.rows.length, review, ok };
}
