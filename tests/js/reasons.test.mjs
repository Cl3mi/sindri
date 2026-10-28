import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EDIT_REASONS, DELETE_REASONS, createReasonSession, classifyKey,
         openReason, settleReasonPrompt } from '../../app/static/js/reasons.js';

test('vocabularies mirror journal.py exactly', () => {
  assert.deepEqual([...EDIT_REASONS].sort(),
    ['misread_digit', 'other', 'wrong_char_type', 'wrong_sign_or_unit', 'wrong_tolerance']);
  assert.deepEqual([...DELETE_REASONS].sort(),
    ['duplicate', 'empty_extraction', 'not_a_characteristic', 'other', 'unballooned_real_dimension', 'wrong_region']);
});

// ---- the session: when an op is applied and what a reason rides on ---------
// Operator feedback 2026-10-28: the card appeared only AFTER the edit and
// vanished on any key. The op is no longer held hostage to the card: it is
// applied at commit, with whatever reason was picked while editing; a reason
// picked afterwards replaces the still-top op with one that carries it, so the
// journal ends exactly as if the reason had been picked up front.

function rig() {
  const log = { applied: [], replaced: [] };
  const io = {
    apply: (op) => log.applied.push(op),
    replaceTop: (oldOp, newOp) => { log.replaced.push([oldOp, newOp]); return rig.top !== false; },
  };
  const makeOp = (reason) => ({ reason });
  return { log, io, makeOp };
}

test('a reason picked while editing rides on the op applied at commit', () => {
  const { log, io, makeOp } = rig();
  const s = createReasonSession('edit', makeOp, io);
  s.pick('misread_digit');
  assert.deepEqual(log.applied, []);            // nothing applied before commit
  s.commit();
  assert.deepEqual(log.applied, [{ reason: 'misread_digit' }]);
  assert.equal(s.phase, 'done');                 // explained: the card has no more to ask
});

test('commit without a reason applies at once and leaves the card open for a late pick', () => {
  const { log, io, makeOp } = rig();
  const s = createReasonSession('edit', makeOp, io);
  s.commit();
  assert.deepEqual(log.applied, [{ reason: undefined }]);
  assert.equal(s.phase, 'applied');
  s.pick('wrong_tolerance');
  assert.equal(log.replaced.length, 1);
  assert.equal(log.replaced[0][0], log.applied[0]);
  assert.deepEqual(log.replaced[0][1], { reason: 'wrong_tolerance' });
  assert.equal(s.phase, 'done');
});

test('a late pick after another op landed changes nothing and does not throw', () => {
  const { log, io, makeOp } = rig();
  rig.top = false;                               // the op is no longer on top of the undo stack
  try {
    const s = createReasonSession('delete', makeOp, io);
    s.commit();
    s.pick('duplicate');
    assert.equal(log.replaced.length, 1);        // asked, was refused
    assert.equal(s.phase, 'done');
  } finally { rig.top = undefined; }
});

test('an off-vocabulary key is ignored, and commit is idempotent', () => {
  const { log, io, makeOp } = rig();
  const s = createReasonSession('edit', makeOp, io);
  s.pick('misread_digit_typo');
  s.commit(); s.commit();
  assert.deepEqual(log.applied, [{ reason: undefined }]);
  assert.equal(s.phase, 'applied');
});

test('delete reasons are validated against the delete vocabulary, not the edit one', () => {
  const { log, io, makeOp } = rig();
  const s = createReasonSession('delete', makeOp, io);
  s.pick('misread_digit');                       // an EDIT reason
  s.commit();
  assert.deepEqual(log.applied, [{ reason: undefined }]);
});

test('cancel before commit never applies (Esc reverted the edit)', () => {
  const { log, io, makeOp } = rig();
  const s = createReasonSession('edit', makeOp, io);
  s.pick('other');
  s.cancel();
  s.commit();
  assert.deepEqual(log.applied, []);
  assert.equal(s.phase, 'done');
});

test('end() closes an applied session without touching the journal', () => {
  const { log, io, makeOp } = rig();
  const s = createReasonSession('edit', makeOp, io);
  s.commit();
  s.end();
  s.pick('other');
  assert.equal(log.replaced.length, 0);
  assert.equal(log.applied.length, 1);
});

// ---- key policy: the card persists, so only a deliberate pick consumes a key --

const key = (k, extra = {}) => ({ key: k, target: { tagName: 'DIV', isContentEditable: false }, ...extra });
const editing = { tagName: 'TD', isContentEditable: true };

test('classifyKey: a plain digit outside an editor picks that chip', () => {
  assert.deepEqual(classifyKey(key('3'), 5), { reason: 2 });
});

test('classifyKey: Alt+digit picks even inside an editor', () => {
  assert.deepEqual(classifyKey(key('2', { target: editing, altKey: true }), 5), { reason: 1 });
});

test('classifyKey: a digit typed into an editor is the reviewer typing, not a pick', () => {
  // Tab-ing to the next cell and typing "12.5" must not be eaten by the card.
  assert.equal(classifyKey(key('1', { target: editing }), 5), 'ignore');
  assert.equal(classifyKey(key('1', { target: { tagName: 'INPUT' } }), 5), 'ignore');
});

test('classifyKey: every other key passes through and leaves the card open', () => {
  assert.equal(classifyKey(key('6'), 5), 'ignore');
  assert.equal(classifyKey(key('1', { ctrlKey: true }), 5), 'ignore');
  assert.equal(classifyKey(key('z', { ctrlKey: true }), 5), 'ignore');
  assert.equal(classifyKey(key('Tab'), 5), 'ignore');
  for (const k of ['Shift', 'Control', 'Alt', 'Meta']) assert.equal(classifyKey(key(k), 5), 'ignore');
  assert.equal(classifyKey(key('a', { isComposing: true }), 5), 'ignore');
});

test('classifyKey: Escape outside an editor closes the card; inside, it belongs to the editor', () => {
  assert.equal(classifyKey(key('Escape'), 5), 'swallow-dismiss');
  assert.equal(classifyKey(key('Escape', { target: editing }), 5), 'ignore');
});

// ---- the card (DOM) ---------------------------------------------------------

function fakeEl(tag = 'div') {
  const el = {
    tagName: tag.toUpperCase(), style: {}, children: [], listeners: {}, removed: false,
    className: '', textContent: '', attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    addEventListener(t, f) { (this.listeners[t] ||= []).push(f); },
    remove() { this.removed = true; },
    appendChild(c) { this.children.push(c); },
    contains() { return false; },
    getBoundingClientRect() { return { left: 0, top: 0, bottom: 0, right: 0, height: 0, width: 0 }; },
    classList: { toggle(c, on) { (el.classes ||= new Set())[on ? 'add' : 'delete'](c); } },
    click() { for (const f of this.listeners.click || []) f({ stopPropagation() {}, preventDefault() {} }); },
  };
  return el;
}
const all = (el) => [el, ...el.children.flatMap(all)];

function withFakeDom(fn) {
  return async () => {
    const body = fakeEl('body');
    globalThis.document = { createElement: fakeEl, body, visibilityState: 'visible',
                            addEventListener() {}, removeEventListener() {} };
    globalThis.window = { innerHeight: 1000, innerWidth: 1000,
                          addEventListener() {}, removeEventListener() {} };
    try { await fn(body); } finally { delete globalThis.document; delete globalThis.window; }
  };
}

const chips = (card) => all(card).filter((e) => e.className.split(' ').includes('chip'));
const closeBtn = (card) => all(card).find((e) => e.className.split(' ').includes('reason-close'));

test('the card has a visible close X; closing hides it but the edit still commits', withFakeDom((body) => {
  const { log, io, makeOp } = rig();
  const s = openReason('edit', makeOp, null, io);
  const card = body.children[0];
  assert.ok(closeBtn(card), 'a close button');
  closeBtn(card).click();
  assert.equal(card.removed, true);
  s.commit();
  assert.deepEqual(log.applied, [{ reason: undefined }]);   // reasonless, nothing lost
}));

test('clicking a chip while editing picks it; the card closes once the op is applied with it', withFakeDom((body) => {
  const { log, io, makeOp } = rig();
  const s = openReason('edit', makeOp, null, io);
  const card = body.children[0];
  chips(card)[0].click();                        // 'Misread digit'
  assert.equal(card.removed, false);             // still up: the edit is not committed yet
  s.commit();
  assert.deepEqual(log.applied, [{ reason: 'misread_digit' }]);
  assert.equal(card.removed, true);
}));

test('the card stays open after an unexplained commit until a chip or X', withFakeDom((body) => {
  const { log, io, makeOp } = rig();
  const s = openReason('delete', makeOp, null, io);
  s.commit();
  const card = body.children[0];
  assert.equal(card.removed, false);
  chips(card)[3].click();                        // 'Wrong region / misplaced box'
  assert.deepEqual(log.replaced[0][1], { reason: 'wrong_region' });
  assert.equal(card.removed, true);
}));

test('a second card replaces the first; settleReasonPrompt closes whatever is open', withFakeDom((body) => {
  const { io, makeOp } = rig();
  openReason('edit', makeOp, null, io);
  const first = body.children[0];
  openReason('edit', makeOp, null, io);
  assert.equal(first.removed, true);
  const second = body.children[1];
  return settleReasonPrompt().then(() => assert.equal(second.removed, true));
}));

test('settleReasonPrompt with nothing open resolves at once', async () => {
  await settleReasonPrompt();
});
