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
