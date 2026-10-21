import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EDIT_REASONS, DELETE_REASONS, commitWithReason } from '../../app/static/js/reasons.js';

test('vocabularies mirror journal.py exactly', () => {
  assert.deepEqual([...EDIT_REASONS].sort(),
    ['misread_digit', 'other', 'wrong_char_type', 'wrong_sign_or_unit', 'wrong_tolerance']);
  assert.deepEqual([...DELETE_REASONS].sort(),
    ['duplicate', 'not_a_characteristic', 'other', 'unballooned_real_dimension', 'wrong_region']);
});

test('the op is applied once, with the chosen reason', async () => {
  const applied = [];
  await commitWithReason('edit', (reason) => ({ reason }), async () => 'misread_digit',
                         (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: 'misread_digit' }]);
});

test('dismissing applies the op with no reason; an off-vocabulary answer counts as none', async () => {
  const applied = [];
  await commitWithReason('delete', (reason) => ({ reason }), async () => undefined,
                         (op) => applied.push(op));
  await commitWithReason('delete', (reason) => ({ reason }), async () => 'misread_digit',
                         (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: undefined }, { reason: undefined }]);
});

test('a failing prompt still applies the op: the edit is never lost', async () => {
  const applied = [];
  await commitWithReason('edit', (reason) => ({ reason }),
                         async () => { throw new Error('no DOM'); }, (op) => applied.push(op));
  assert.deepEqual(applied, [{ reason: undefined }]);
});

import { classifyKey } from '../../app/static/js/reasons.js';

const key = (k, extra = {}) => ({ key: k, target: { tagName: 'DIV', isContentEditable: false }, ...extra });
const editing = { tagName: 'TD', isContentEditable: true };

test('classifyKey: a digit outside an editor picks that chip', () => {
  assert.deepEqual(classifyKey(key('3'), 5), { reason: 2 });
});

test('classifyKey: a digit typed into an editor dismisses and passes through', () => {
  // Tab-ing to the next cell and typing "12.5" must not be eaten by the popover.
  assert.equal(classifyKey(key('1', { target: editing }), 5), 'dismiss');
  assert.equal(classifyKey(key('1', { target: { tagName: 'INPUT' } }), 5), 'dismiss');
});

test('classifyKey: out-of-range digit, modifiers and other keys dismiss', () => {
  assert.equal(classifyKey(key('6'), 5), 'dismiss');
  assert.equal(classifyKey(key('1', { ctrlKey: true }), 5), 'dismiss');
  assert.equal(classifyKey(key('z', { ctrlKey: true }), 5), 'dismiss');
  assert.equal(classifyKey(key('Tab'), 5), 'dismiss');
});

test('classifyKey: modifier-only keys and IME composition keep it open', () => {
  for (const k of ['Shift', 'Control', 'Alt', 'Meta']) assert.equal(classifyKey(key(k), 5), 'ignore');
  assert.equal(classifyKey(key('a', { isComposing: true }), 5), 'ignore');
});

test('classifyKey: Escape is swallowed and dismisses', () => {
  assert.equal(classifyKey(key('Escape'), 5), 'swallow-dismiss');
});

// ---- settleReasonPrompt must be awaitable --------------------------------
// main.js settles any open prompt before it routes away, seals (Finish) or
// swaps the open drawing, and then reads journal state (pending events,
// the Finish snapshot). Resolving the popover is not enough: commitWithReason
// applies the op only after its own `await ask()` continuation runs, so a
// caller that read the journal straight after settling would miss the very
// edit the popover was holding -- it would be sealed out of the revision or
// wiped by journal.resume(). The returned promise resolves only once every
// in-flight commit has APPLIED its op, however many async hops the `ask`
// wrapper adds between askReason and commitWithReason.

import { askReason, settleReasonPrompt } from '../../app/static/js/reasons.js';

function fakeEl() {
  return {
    style: {}, children: [],
    setAttribute() {}, addEventListener() {}, remove() {},
    appendChild(c) { this.children.push(c); },
    contains() { return false; },
    getBoundingClientRect() { return { left: 0, top: 0, bottom: 0, right: 0, height: 0, width: 0 }; },
  };
}

function withFakeDom(fn) {
  return async () => {
    globalThis.document = {
      createElement: fakeEl, body: fakeEl(), visibilityState: 'visible',
      addEventListener() {}, removeEventListener() {},
    };
    globalThis.window = { innerHeight: 1000, innerWidth: 1000,
                          addEventListener() {}, removeEventListener() {} };
    try { await fn(); } finally { delete globalThis.document; delete globalThis.window; }
  };
}

test('settleReasonPrompt resolves only after the held op has been applied', withFakeDom(async () => {
  const applied = [];
  // Extra async hops on purpose: a caller-side `await Promise.resolve()`
  // would race these and lose; the settle promise must not.
  const ask = async (kind) => { await null; return askReason(kind, null); };
  const done = commitWithReason('delete', (reason) => ({ reason }), ask, (op) => applied.push(op));
  await new Promise((r) => setTimeout(r, 0));   // the popover is now open
  assert.deepEqual(applied, []);
  await settleReasonPrompt();
  assert.deepEqual(applied, [{ reason: undefined }]);
  await done;
}));

test('settleReasonPrompt with nothing open resolves at once', async () => {
  await settleReasonPrompt();
});

test('settleReasonPrompt still resolves when the held op throws on apply', withFakeDom(async () => {
  // A failing apply is the commit's own failure; it must not also wedge
  // navigation or Finish, which await this settle before doing anything.
  const done = commitWithReason('edit', (reason) => ({ reason }),
                                (kind) => askReason(kind, null),
                                () => { throw new Error('apply failed'); });
  await settleReasonPrompt();
  await assert.rejects(done, /apply failed/);
}));
