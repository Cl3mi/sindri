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
  marks: null,
  title_block: [],
  selectedId: null,  // selected marker / row id
  hoverId: null,
  filter: 'all',     // 'all' | 'review' | 'ok' | 'suggested'
  search: '',
  ocrBackend: '—',
  ocrOk: null,       // null | true | false
};

export function setSession(payload) {
  state.sessionId = payload.session_id;
  state.imageUrl  = payload.image_url;
  state.rows      = payload.rows.map((r) => ({ ...r, reviewed: false }));
  state.notes     = payload.notes;
  state.marks     = payload.marks ?? null;
  state.title_block = payload.title_block ?? [];
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
  state.marks     = null;
  state.title_block = [];
  state.selectedId = null;
  undoStack.length = 0;
  redoStack.length = 0;
  emit('session', state);
  emit('change');
}

// ===== Undo / redo ====================================================
const undoStack = [];
const redoStack = [];

// Every op carries event(): the review journal's self-description of it
// (journal.js). Python mirrors these semantics in app/review/replay.py --
// if that file falls out of step with this one, seal reports mismatches on
// healthy sessions.
export function apply(op) {
  op.do();
  undoStack.push(op);
  redoStack.length = 0;
  emit('op', { kind: 'do', op });
  emit('change');
  emit('history');
}
export function undo() {
  const op = undoStack.pop();
  if (!op) return;
  op.undo();
  redoStack.push(op);
  emit('op', { kind: 'undo', op });
  emit('change');
  emit('history');
}
export function redo() {
  const op = redoStack.pop();
  if (!op) return;
  op.do();
  undoStack.push(op);
  emit('op', { kind: 'do', op });
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
  // Suggestions are not balloons: they keep pos 0 until confirmed, so the
  // numbers a reviewer sees are exactly the numbers that will be exported.
  let n = 0;
  state.rows.forEach((r) => (r.pos = r.suggested ? 0 : ++n));
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
    // JSON clone, so a later edit of the row cannot rewrite the logged event
    event: () => ({ type: 'add_row', row: JSON.parse(JSON.stringify(row)) }),
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
    event: () => ({ type: 'delete_row', id }),
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
    event: () => ({ type: 'move_row', id, xy: [...newXY] }),
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
    // evaluated after do(), so oldValue is the captured one
    event: () => ({ type: 'edit_cell', id, field, old: oldValue ?? '', new: newValue }),
  };
}

// Confirm low-confidence suggestions: each becomes a normal, numbered,
// reviewed balloon. Undo restores the suggestion exactly.
export function opConfirmSuggestions(ids) {
  ids = [...new Set(ids)];   // a dup made replay diverge from export; see opBulkReview
  const confirmed = [];
  return {
    label: 'confirm suggestions',
    do() {
      confirmed.length = 0;
      for (const id of ids) {
        const r = state.rows.find((x) => x.id === id);
        if (r && r.suggested) {
          confirmed.push([id, r.reviewed]);
          r.suggested = false;
          r.reviewed = true;
        }
      }
      renumber();
    },
    undo() {
      for (const [id, wasReviewed] of confirmed) {
        const r = state.rows.find((x) => x.id === id);
        if (r) { r.suggested = true; r.reviewed = wasReviewed; }
      }
      renumber();
    },
    event: () => ({ type: 'confirm_suggestions', ids: [...ids] }),
  };
}

export function opBulkReview(ids, target /* true|false */) {
  // A duplicate id made this op's own prev-map overwrite the original value
  // with the value just applied (do() visits it twice), so undo "restored"
  // the applied value instead of the original -- unrestorable, and the
  // divergence this caused between replay and export was the fuzzer's
  // biggest finding (124 mismatches with dup ids, 0 without).
  ids = [...new Set(ids)];
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
    event: () => ({ type: target ? 'accept' : 'unaccept', ids: [...ids] }),
  };
}

// ===== Filtering / counts ============================================
export function isVisibleRow(r) {
  if (state.filter === 'suggested') { if (!r.suggested) return false; }
  else if (state.filter !== 'all' && r.suggested) return false;
  if (state.filter === 'review' && !(r.needs_review && !r.reviewed)) return false;
  if (state.filter === 'ok'     &&  (r.needs_review && !r.reviewed)) return false;
  const q = state.search.trim().toLowerCase();
  if (!q) return true;
  const hay = `${r.pos} ${r.char_type} ${r.nominal} ${r.upper_tol} ${r.lower_tol}`.toLowerCase();
  return hay.includes(q);
}
export function counts() {
  let review = 0, ok = 0, suggested = 0;
  for (const r of state.rows) {
    if (r.suggested) suggested++;
    else if (r.needs_review && !r.reviewed) review++;
    else ok++;
  }
  return { all: state.rows.length, review, ok, suggested };
}

// The review queue (Phase 1: flagged balloons). Unflagged balloons are counted
// apart as `unchecked`: the system accepted them and nobody was asked to look,
// so an explicit accept on one is not evidence it was checked either.
// Suggestions are not balloons and are neither.
export function progress() {
  const balloons = state.rows.filter((r) => !r.suggested);
  const queue = balloons.filter((r) => r.needs_review);
  const resolved = queue.filter((r) => r.reviewed).length;
  return { resolved, total: queue.length, outstanding: queue.length - resolved,
           unchecked: balloons.length - queue.length };
}
export const canFinish = () => progress().outstanding === 0;
