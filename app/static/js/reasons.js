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
export function commitWithReason(kind, makeOp, ask, apply) {
  const vocab = kind === 'edit' ? EDIT_REASONS : DELETE_REASONS;
  const p = (async () => {
    let reason;
    try {
      reason = await ask(kind);
    } catch {
      reason = undefined;
    }
    apply(makeOp(vocab.has(reason) ? reason : undefined));
  })();
  // Tracked until APPLIED, so settleReasonPrompt can wait for the op itself
  // rather than only for the popover to close (see there).
  inflight.add(p);
  const forget = () => inflight.delete(p);
  p.then(forget, forget);
  return p;
}

// Commits whose op is not applied yet. Usually 0 or 1 (one popover at a
// time), briefly 2 when a second prompt displaces the first. Production
// `ask`s call askReason synchronously, so every entry here already owns
// `current` (or has resolved); an `ask` that deferred opening its popover
// past a settle would open one nobody resolves, and the settle would wait.
const inflight = new Set();

// Pure key policy for the open popover. The popover must never steal a
// keystroke meant for the next cell: after an edit the reviewer Tabs on and
// types "12.5", and swallowing that "1" would record a reason nobody chose
// and corrupt the cell. So a digit only picks a chip when focus is NOT in an
// editor; every other key dismisses reasonless and proceeds untouched (Ctrl+Z
// then undoes the edit just applied). Escape alone is swallowed.
// Returns 'ignore' | 'dismiss' | 'swallow-dismiss' | { reason: index }.
export function classifyKey(e, n) {
  if (e.isComposing || ['Shift', 'Control', 'Alt', 'Meta'].includes(e.key)) return 'ignore';
  if (e.key === 'Escape') return 'swallow-dismiss';
  const t = e.target;
  const editing = !!t && (t.isContentEditable
    || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));
  const d = /^[1-9]$/.test(e.key) ? Number(e.key) : 0;
  if (d >= 1 && d <= n && !e.ctrlKey && !e.metaKey && !e.altKey && !editing) {
    return { reason: d - 1 };
  }
  return 'dismiss';
}

// At most one prompt is open. A second askReason resolves the older one
// reasonless first (its op applies first, preserving order).
let current = null;

// Callers about to navigate away, seal or swap the drawing call this so no
// edit is held hostage by an open popover -- and AWAIT it. Resolving the
// popover only queues commitWithReason's continuation; the op lands a
// microtask (or more, if `ask` wraps askReason) later. A caller that read
// the journal before then would seal the revision without that edit, or let
// journal.resume() wipe it. So this resolves once every in-flight commit has
// applied its op. Never rejects: a throwing apply is the commit's own
// failure, and must not also block navigation or Finish.
export function settleReasonPrompt() {
  current?.(undefined);
  return Promise.allSettled([...inflight]).then(() => {});
}

// The popover itself (DOM). Digits pick a chip (see classifyKey); an outside
// click, navigation, page hide or tab hide resolve with no reason.
export function askReason(kind, anchorEl) {
  if (current) current(undefined);
  return new Promise((resolve) => {
    const el = document.createElement('div');
    el.className = 'reason-popover';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', kind === 'edit' ? 'Reason for edit' : 'Reason for delete');
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
    // Measure after insertion: flip above the anchor when it would overflow
    // the bottom, and keep it inside the right edge.
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
      if (c === 'dismiss') return done(undefined); // keystroke proceeds
      e.preventDefault(); e.stopImmediatePropagation();
      done(c === 'swallow-dismiss' ? undefined : entries[c.reason][0]);
    }
    function onClick(e) { if (!el.contains(e.target)) done(undefined); }
    function onHide() { if (document.visibilityState === 'hidden') done(undefined); }
    const settle = () => done(undefined);
    function done(reason) {
      document.removeEventListener('keydown', onKey, true);
      document.removeEventListener('mousedown', onClick, true);
      document.removeEventListener('visibilitychange', onHide);
      window.removeEventListener('hashchange', settle);
      window.removeEventListener('pagehide', settle);
      el.remove();
      if (current === done) current = null;
      resolve(reason);
    }
    current = done;
    // capture phase on document: runs before shortcuts.js / viewer.js, whose
    // keydown listeners are on window in the bubble phase.
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('mousedown', onClick, true);
    document.addEventListener('visibilitychange', onHide);
    window.addEventListener('hashchange', settle);
    window.addEventListener('pagehide', settle);
  });
}
