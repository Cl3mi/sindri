// Reason chips after an edit or delete (HITL design §1; campaign plan C1
// decisions 6-7). The vocabularies mirror app/review/journal.py's
// EDIT_REASONS / DELETE_REASONS; tests/js/reasons.test.mjs pins the mirror.
// HITL phase 2 imports these rather than defining its own.
//
// Never send a key outside these sets: the server refuses an unknown reason
// at append, which stalls the reviewer's whole journal.

export const EDIT_REASONS = new Set([
  'misread_digit', 'wrong_tolerance', 'wrong_char_type', 'wrong_sign_or_unit', 'other',
]);
export const DELETE_REASONS = new Set([
  'duplicate', 'not_a_characteristic', 'unballooned_real_dimension', 'wrong_region', 'other',
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
    ['other', 'Other'],
  ],
};

// Apply-on-resolution (decision 7): the op is built and applied ONCE, after
// the prompt resolves or is dismissed, so the reason is part of the very
// event the journal sends. `ask(kind)` resolves to a reason key or
// undefined; it may throw (no DOM, popover torn down) and the edit still
// lands, reasonless -- a reviewer's change is never lost to a chip.
export async function commitWithReason(kind, makeOp, ask, apply) {
  const vocab = kind === 'edit' ? EDIT_REASONS : DELETE_REASONS;
  let reason;
  try {
    reason = await ask(kind);
  } catch {
    reason = undefined;
  }
  apply(makeOp(vocab.has(reason) ? reason : undefined));
}

// The popover itself (DOM). Keys 1-5 pick a chip while it is open and are
// swallowed so the global filter-pill shortcuts never see them; any other
// key or an outside click dismisses with no reason.
export function askReason(kind, anchorEl) {
  return new Promise((resolve) => {
    const el = document.createElement('div');
    el.className = 'reason-popover';
    const title = document.createElement('div');
    title.className = 'reason-title';
    title.textContent = kind === 'edit' ? 'Why was it wrong? (optional)' : 'Why delete it? (optional)';
    el.appendChild(title);
    const entries = REASON_LABELS[kind];
    entries.forEach(([key, label], i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'chip';
      b.textContent = `${i + 1} ${label}`;
      b.addEventListener('click', (e) => { e.stopPropagation(); done(key); });
      el.appendChild(b);
    });
    const r = anchorEl?.getBoundingClientRect?.();
    Object.assign(el.style, { position: 'fixed',
      left: `${Math.max(8, r ? r.left : 40)}px`, top: `${r ? r.bottom + 4 : 40}px` });
    document.body.appendChild(el);

    function onKey(e) {
      e.stopPropagation(); e.preventDefault();
      const n = Number(e.key);
      done(n >= 1 && n <= entries.length ? entries[n - 1][0] : undefined);
    }
    function onClick(e) { if (!el.contains(e.target)) done(undefined); }
    function done(reason) {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onClick, true);
      el.remove();
      resolve(reason);
    }
    // capture phase on document: runs before shortcuts.js / viewer.js, whose
    // keydown listeners are on window in the bubble phase.
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onClick, true);
  });
}
