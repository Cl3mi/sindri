// Staging badge texts (batch design §2) from a staging row
// (app/batch/staging.py staging_row).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { badgesFor } = await import('../../app/static/js/badges.js');

const row = (kw = {}) => ({ pages: 1, oversized: false, unreadable: false, duplicate_of: null, ...kw });

test('a normal drawing carries no badge', () => {
  assert.deepEqual(badgesFor(row()), []);
});

test("multi-page and oversized carry the design's warnings", () => {
  assert.deepEqual(badgesFor(row({ pages: 3, oversized: true })).map((b) => b.text), [
    'multi-page: only sheet 1 is read; extraction quality may be poor',
    'oversized: read at reduced resolution; extraction quality may be poor',
  ]);
});

test('a duplicate names the original, its batch and its state', () => {
  const dup = { id: 'x', file_name: 'Flansch.pdf', batch_id: 'b', batch_name: 'Januar', state: 'in_review' };
  assert.deepEqual(badgesFor(row({ duplicate_of: dup })), [
    { kind: 'duplicate', text: 'duplicate of "Flansch.pdf" (batch Januar, in review)' }]);
});

test('an unreadable PDF says only that', () => {
  assert.deepEqual(badgesFor(row({ unreadable: true, pages: null, oversized: null })),
    [{ kind: 'unreadable', text: 'unreadable PDF' }]);
});

test('a sheet never measured (staged before B2) is not called oversized', () => {
  assert.deepEqual(badgesFor(row({ oversized: null })), []);
});

const { workListBadges } = await import('../../app/static/js/badges.js');

test('workListBadges shows an exported revision', () => {
  const b = workListBadges({ final_revision: 2, exported_revision: 2, out_of_scope: false,
                             allowed_out_of_scope: false, state: 'finished', error_type: null });
  assert.deepEqual(b, [{ kind: 'exported', text: 'exported r2' }]);
});

test('workListBadges flags a newer revision than what was exported', () => {
  const b = workListBadges({ final_revision: 3, exported_revision: 2, out_of_scope: false,
                             allowed_out_of_scope: false, state: 'finished', error_type: null });
  assert.deepEqual(b, [{ kind: 'exported_stale', text: 'exported r2 (newer revision r3 not exported)' }]);
});

test('workListBadges names a failed drawing\'s error type', () => {
  const b = workListBadges({ final_revision: null, exported_revision: null, out_of_scope: false,
                             allowed_out_of_scope: false, state: 'failed', error_type: 'RuntimeError' });
  assert.deepEqual(b, [{ kind: 'failed', text: 'failed (RuntimeError)' }]);
});

test('workListBadges marks an out-of-scope drawing included by permission', () => {
  const b = workListBadges({ final_revision: null, exported_revision: null, out_of_scope: true,
                             allowed_out_of_scope: true, state: 'ready', error_type: null });
  assert.deepEqual(b, [{ kind: 'out_of_scope', text: 'out of scope (included)' }]);
});

test('workListBadges is empty for an ordinary in-progress drawing', () => {
  assert.deepEqual(workListBadges({ final_revision: null, exported_revision: null, out_of_scope: false,
                                    allowed_out_of_scope: false, state: 'queued', error_type: null }), []);
});

// Retention's tombstone (batch design §4, decision 6/8) had no label at all
// before this (Task 9/13 follow-up): a purged row looked like any other
// finished drawing except for its bare state chip reading "purged".
test('workListBadges names a purged (retention-deleted) drawing', () => {
  const b = workListBadges({ final_revision: 2, exported_revision: 2, out_of_scope: false,
                             allowed_out_of_scope: false, state: 'purged', error_type: null });
  assert.deepEqual(b, [{ kind: 'exported', text: 'exported r2' }, { kind: 'purged', text: 'deleted by retention' }]);
});

const { stateLabel } = await import('../../app/static/js/badges.js');

test('stateLabel reads a purged drawing as a tombstone, not the raw state word', () => {
  assert.equal(stateLabel('purged'), 'Deleted (retention)');
});

test('stateLabel falls back to the state word with underscores spaced out', () => {
  assert.equal(stateLabel('in_review'), 'in review');
  assert.equal(stateLabel('finished'), 'finished');
});
