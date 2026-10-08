// Two states this one state machine covers (batch design §3, §5): the losing
// side of a two-tab takeover (stale, unrecoverable -- reload) and a journal
// that keeps failing to flush (lagging, recoverable -- retry countdown).
// Pure; the DOM (showing/hiding the two banner elements) is wired in
// main.js (Task 14/15 together own banner.js's DOM half).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { bannerFor } = await import('../../app/static/js/banner.js');

test('no banner when nothing is wrong', () => {
  assert.equal(bannerFor({ stale: false, lagging: false }), null);
});

test('stale wins over lagging -- once another tab has the writer, retrying is pointless', () => {
  assert.deepEqual(bannerFor({ stale: true, lagging: true, unsavedCount: 2 }),
    { kind: 'stale', unsavedCount: 2 });
});

test('lagging reports a retry countdown in whole seconds, never negative', () => {
  assert.deepEqual(bannerFor({ stale: false, lagging: true, retryAt: 5000, now: 2000 }),
    { kind: 'lagging', secondsLeft: 3 });
  assert.deepEqual(bannerFor({ stale: false, lagging: true, retryAt: 1000, now: 2000 }),
    { kind: 'lagging', secondsLeft: 0 });
});
