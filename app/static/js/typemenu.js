// The suggestion list under a Characteristic cell (chartypes.js has the
// vocabulary and the filtering). The cell stays a contentEditable <td>, not a
// <select>/<input>: table.js hands the focused cell, its unsaved text and its
// caret across every table rebuild, and that logic is built on the TD.
//
// The menu never takes focus. A mousedown on an option is cancelled so the
// cell keeps focus until the pick sets its text and blurs it, which commits
// through the cell's own blur handler -- one commit path for typed and picked.
import { suggest, moveHighlight } from './chartypes.js';

let open = null;   // { el, td, items, hi }

export function closeTypeMenu() {
  if (!open) return;
  open.el.remove();
  open = null;
}

function paint() {
  const { el, items, hi, td } = open;
  el.innerHTML = '';
  items.forEach((name, i) => {
    const d = document.createElement('div');
    d.className = 'type-opt' + (i === hi ? ' hi' : '');
    d.setAttribute('role', 'option');
    d.textContent = name;
    d.addEventListener('mousedown', (e) => {
      e.preventDefault();
      td.textContent = name;
      td.blur();
    });
    el.appendChild(d);
  });
  el.hidden = items.length === 0;
}

function refresh(td) {
  if (!open || open.td !== td) return;
  open.items = suggest(td.textContent, td.dataset._orig);
  open.hi = -1;
  paint();
}

// Wire a Characteristic cell. Called once per cell render.
export function attachTypeMenu(td) {
  td.addEventListener('focus', () => {
    closeTypeMenu();
    const el = document.createElement('div');
    el.className = 'type-menu';
    el.setAttribute('role', 'listbox');
    const r = td.getBoundingClientRect();
    Object.assign(el.style, { position: 'fixed', left: `${r.left}px`, top: `${r.bottom + 2}px` });
    document.body.appendChild(el);
    open = { el, td, items: [], hi: -1 };
    // _orig is set by the cell's own focus handler, registered before this.
    refresh(td);
    const box = el.getBoundingClientRect();
    if (box.bottom > window.innerHeight - 8) {
      el.style.top = `${Math.max(8, r.top - box.height - 2)}px`;
    }
  });
  td.addEventListener('input', () => refresh(td));
  td.addEventListener('blur', () => { if (open?.td === td) closeTypeMenu(); });
  // Capture phase would be too early for the cell's own Enter handling; this
  // runs first only because it is registered before it (table.js).
  td.addEventListener('keydown', (e) => {
    if (!open || open.td !== td) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      open.hi = moveHighlight(open.hi, e.key === 'ArrowDown' ? 1 : -1, open.items.length);
      paint();
    } else if (e.key === 'Enter' && open.hi >= 0) {
      // Pick the highlighted option; a plain Enter (no highlight) still
      // commits the typed text through the cell's handler.
      e.preventDefault();
      e.stopImmediatePropagation();
      td.textContent = open.items[open.hi];
      td.blur();
    } else if (e.key === 'Escape') {
      closeTypeMenu();
    }
  });
}
