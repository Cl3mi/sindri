// Reason chips after an edit or delete (HITL design §1; campaign plan C1
// decisions 6-7; reworked in review UX batch 3, 2026-10-28). The vocabularies mirror app/review/journal.py's
// EDIT_REASONS / DELETE_REASONS; tests/js/reasons.test.mjs pins the mirror.
// HITL phase 2 imports these rather than defining its own.
//
// Never send a key outside these sets: the server refuses an unknown reason
// at append, which stalls the reviewer's whole journal.

export const EDIT_REASONS = new Set([
  'misread_digit', 'wrong_tolerance', 'wrong_char_type', 'wrong_sign_or_unit', 'other',
]);
export const DELETE_REASONS = new Set([
  'duplicate', 'not_a_characteristic', 'unballooned_real_dimension', 'wrong_region',
  'empty_extraction', 'other',
]);

export const REASON_LABELS = {
  edit: [
    ['misread_digit', 'Misread digit'],
    ['wrong_tolerance', 'Wrong/missing tolerance'],
    ['wrong_char_type', 'Wrong characteristic type'],
    ['wrong_sign_or_unit', 'Wrong sign or unit'],
    ['other', 'Other'],
  ],
  delete: [
    ['duplicate', 'Duplicate of another balloon'],
    ['not_a_characteristic', 'Not a characteristic (text, title block…)'],
    ['unballooned_real_dimension', "Real dimension, but we don't balloon it"],
    ['wrong_region', 'Wrong region / misplaced box'],
    ['empty_extraction', 'Empty extraction (nothing read)'],
    ['other', 'Other'],
  ],
};

// ---- the session -------------------------------------------------------
// Batch 3 (operator feedback): the card used to open only AFTER the edit and
// vanished on any key or click, with the op held until it resolved (C1
// decision 7, "apply-on-resolution"). Now the op is applied at COMMIT, never
// held for the card, and the card is just a way to attach a reason:
//
//   * picked before commit  -> the reason rides on the op, as before;
//   * picked after commit   -> `io.replaceTop` swaps the still-top op for one
//     that carries the reason (undo + apply), so the journal nets out exactly
//     as if it had been picked up front. If anything else was done since, the
//     op is no longer top, the swap is refused and the pick is a no-op:
//     a reason must never be attached to a different state than it explained.
//
// `io` is { apply(op), replaceTop(oldOp, newOp) -> bool, onOp?(fn) -> off }.
export function createReasonSession(kind, makeOp, io) {
  const vocab = kind === 'edit' ? EDIT_REASONS : DELETE_REASONS;
  const subs = new Set();
  const s = {
    kind, op: null, picked: undefined, phase: 'editing',   // editing | applied | done
    onChange(fn) { subs.add(fn); return () => subs.delete(fn); },
    pick(key) {
      if (s.phase === 'done' || !vocab.has(key)) return;
      s.picked = key;
      if (s.phase === 'applied') {
        const next = makeOp(key);
        s.busy = true;
        try { io.replaceTop(s.op, next); } finally { s.busy = false; }
        s.phase = 'done';
      }
      notify();
    },
    commit() {
      if (s.phase !== 'editing') return;
      s.op = makeOp(s.picked);
      s.phase = s.picked ? 'done' : 'applied';
      s.busy = true;                 // our own op is not a "foreign" one
      try { io.apply(s.op); } finally { s.busy = false; }
      notify();
    },
    cancel() { if (s.phase === 'editing') { s.phase = 'done'; notify(); } },
    end() { if (s.phase !== 'done') { s.phase = 'done'; notify(); } },
  };
  const notify = () => subs.forEach((f) => f(s));
  return s;
}

// Pure key policy for the open card. The card now persists, so a key it does
// not own must never touch it: typing "12.5" into the next cell is typing,
// not a pick. A plain digit picks only outside an editor; Alt+digit picks
// anywhere (best effort: some browsers keep Alt+digit for tab switching).
// Escape closes the card, except inside an editor, where it reverts the edit.
// Returns 'ignore' | 'swallow-dismiss' | { reason: index }.
export function classifyKey(e, n) {
  if (e.isComposing || ['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) return 'ignore';
  const t = e.target;
  const inEditor = !!t && (t.isContentEditable
    || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));
  if (e.key === 'Escape') return inEditor ? 'ignore' : 'swallow-dismiss';
  const d = /^[1-9]$/.test(e.key) ? Number(e.key) : 0;
  if (d >= 1 && d <= n && !e.ctrlKey && !e.metaKey && (e.altKey || !inEditor)) {
    return { reason: d - 1 };
  }
  return 'ignore';
}

// At most one card. Opening a second ends the first (its op, if any, is
// already applied: nothing is lost by closing it).
let current = null;

// Callers about to navigate away, seal or swap the drawing call this. Nothing
// is held back any more, so it only closes the card; it stays awaitable and
// never rejects so those callers need not change.
export function settleReasonPrompt() {
  current?.();
  return Promise.resolve();
}

// Opens the card for a new session and returns it. `anchorEl` is only used for
// placement (its rect is read now, so a deleted balloon's card still sits where
// the balloon was). The caller drives the session: commit() when the edit lands
// (immediately, for a delete), cancel() if it is abandoned.
export function openReason(kind, makeOp, anchorEl, io) {
  if (current) current();
  const session = createReasonSession(kind, makeOp, io);
  const entries = REASON_LABELS[kind];
  const el = document.createElement('div');
  el.className = 'reason-popover';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-label', kind === 'edit' ? 'Reason for edit' : 'Reason for delete');

  const head = document.createElement('div');
  head.className = 'reason-head';
  const title = document.createElement('div');
  title.className = 'reason-title';
  title.textContent = kind === 'edit' ? 'Why was it wrong? (optional)' : 'Why delete it? (optional)';
  const x = document.createElement('button');
  x.type = 'button';
  x.className = 'reason-close';
  x.setAttribute('aria-label', 'Close without a reason');
  x.title = 'Close (no reason)';
  x.textContent = '×';
  x.addEventListener('click', (e) => { e.stopPropagation(); hide(); });
  head.appendChild(title);
  head.appendChild(x);
  el.appendChild(head);

  const chipEls = entries.map(([key, label], i) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chip';
    b.textContent = `${i + 1} ${label}`;
    // mousedown must not steal focus from the cell still being edited.
    b.addEventListener('mousedown', (e) => e.preventDefault());
    b.addEventListener('click', (e) => { e.stopPropagation(); session.pick(key); });
    el.appendChild(b);
    return b;
  });

  const r = anchorEl?.getBoundingClientRect?.();
  Object.assign(el.style, { position: 'fixed',
    left: `${Math.max(8, r ? r.left : 40)}px`, top: `${r ? r.bottom + 4 : 40}px` });
  document.body.appendChild(el);
  // Measure after insertion: flip above the anchor when it would overflow the
  // bottom, and keep it inside the right edge.
  const box = el.getBoundingClientRect();
  if (r && box.bottom > window.innerHeight - 8) {
    el.style.top = `${Math.max(8, r.top - box.height - 4)}px`;
  }
  if (box.right > window.innerWidth - 8) {
    el.style.left = `${Math.max(8, window.innerWidth - box.width - 8)}px`;
  }

  function onKey(e) {
    const c = classifyKey(e, entries.length);
    if (c === 'ignore') return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (c === 'swallow-dismiss') hide();
    else session.pick(entries[c.reason][0]);
  }
  // A card is hidden, not the session ended: an edit still being typed
  // commits (reasonless) at blur whether or not the card is on screen.
  let hidden = false;
  const offOp = io.onOp?.((ev) => { if (!session.busy && ev?.op !== session.op) session.end(); });
  const settle = () => { session.end(); hide(); };
  function hide() {
    if (hidden) return;
    hidden = true;
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('hashchange', settle);
    window.removeEventListener('pagehide', settle);
    offOp?.();
    el.remove();
    if (current === settle) current = null;
  }
  session.onChange((s) => {
    chipEls.forEach((b, i) => b.classList.toggle('picked', entries[i][0] === s.picked));
    if (s.phase === 'done') hide();
  });
  current = settle;
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('hashchange', settle);
  window.addEventListener('pagehide', settle);
  return session;
}
