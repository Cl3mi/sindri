// The hash router (batch design §3): #/ work list, #/staging/<batch> staging,
// #/doc/<id> review. Pure parsing only; the dispatcher that drives the three
// screens is wired in main.js (DOM-only, checked by import, Task 10/12/13).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { parseRoute, docUrl, stagingUrl, navigate } = await import('../../app/static/js/router.js');

test('an empty or bare hash is the work list', () => {
  assert.deepEqual(parseRoute(''), { name: 'worklist' });
  assert.deepEqual(parseRoute('#'), { name: 'worklist' });
  assert.deepEqual(parseRoute('#/'), { name: 'worklist' });
});

test('#/doc/<id> is the review screen', () => {
  const id = 'a'.repeat(32);
  assert.deepEqual(parseRoute(`#/doc/${id}`), { name: 'doc', id });
});

test('#/staging/<batch> is the staging screen', () => {
  const id = 'b'.repeat(32);
  assert.deepEqual(parseRoute(`#/staging/${id}`), { name: 'staging', batchId: id });
});

test('a malformed id falls back to the work list rather than crashing', () => {
  assert.deepEqual(parseRoute('#/doc/not-an-id'), { name: 'worklist' });
  assert.deepEqual(parseRoute('#/doc/'), { name: 'worklist' });
  assert.deepEqual(parseRoute('#/bogus'), { name: 'worklist' });
});

test('legacy #doc=<id> bookmarks (B1/B2) still open their drawing', () => {
  const id = 'd'.repeat(32);
  assert.deepEqual(parseRoute(`#doc=${id}`), { name: 'doc', id });
});

test('a legacy #doc= bookmark with an invalid id still falls back to the work list', () => {
  assert.deepEqual(parseRoute('#doc=not-an-id'), { name: 'worklist' });
  assert.deepEqual(parseRoute('#doc='), { name: 'worklist' });
});

test('docUrl and stagingUrl build the fragment parseRoute reads back', () => {
  // Round-trip: the hash this module WRITES must be one it can also READ,
  // or navigate(docUrl(id)) would navigate to a route parseRoute refuses.
  const id = 'c'.repeat(32);
  assert.deepEqual(parseRoute(docUrl(id)), { name: 'doc', id });
  assert.deepEqual(parseRoute(stagingUrl(id)), { name: 'staging', batchId: id });
});

// `navigate` is the fix for a real bug: `history.replaceState` (what B1's
// setHash() called directly) never fires `hashchange` -- that event only
// fires for a navigation the BROWSER performs. Without this seam, every
// button that only changed the URL (Prev, Skip, Jump, Finish's chain-to-
// next, the post-import redirect) was a silent no-op: the address bar
// changed and the screen did not (decision 10). `setUrl`/`dispatch` are
// injected so this stays testable without `window.history` or a real
// router -- `main.js`'s `go()` (Task 13) is the only caller that passes the
// real ones.
test('navigate writes the url THEN dispatches, in that order', () => {
  const calls = [];
  navigate('#/doc/x', {
    setUrl: (u) => calls.push(['setUrl', u]),
    dispatch: () => calls.push(['dispatch']),
  });
  assert.deepEqual(calls, [['setUrl', '#/doc/x'], ['dispatch']]);
});

test('navigate tolerates missing setUrl/dispatch (never throws)', () => {
  assert.doesNotThrow(() => navigate('#/doc/x'));
  assert.doesNotThrow(() => navigate('#/doc/x', {}));
});
