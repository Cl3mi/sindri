// Staging screen: mostly DOM (a draft's rows, Include toggles, Start,
// Discard, remove-one-row), so -- like worklist.js's DOM half -- this is an
// import/smoke check, not a behaviour test: it catches a renamed or dropped
// export, which is the failure mode that matters once main.js wires this in
// (Task 13).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const mod = await import('../../app/static/js/stagingview.js');

test('initStagingView is exported and callable with no DOM staging elements present', () => {
  assert.equal(typeof mod.initStagingView, 'function');
  assert.doesNotThrow(() => mod.initStagingView({}));
});

test('ignoredSummary formats the capped-list-plus-totals shape uploads.py sends', () => {
  const { ignoredSummary } = mod;
  assert.equal(ignoredSummary({ ignored: [], ignored_total: 0, ignored_by_reason: {} }), '');
  assert.equal(
    ignoredSummary({ ignored: ['a.txt'], ignored_total: 1, ignored_by_reason: { non_pdf: 1 } }),
    '1 file ignored (non pdf)');
  assert.equal(
    ignoredSummary({ ignored: [], ignored_total: 1200, ignored_by_reason: { non_pdf: 1000, unreadable_entry: 200 } }),
    '1200 files ignored (non pdf, unreadable entry)');
});
