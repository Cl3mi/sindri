// Work-list pure logic (batch design §3): client-side filtering of an
// already-fetched page (the server also filters; this is what a typed
// search or a clicked filter pill applies instantly, before the next poll
// round-trips), and the queue header's text. DOM rendering and polling are
// wired in main.js (Task 10, import-checked only).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { filterDocuments, formatEta, formatQueueHeader } =
  await import('../../app/static/js/worklist.js');

const DOCS = [
  { id: 'a', file_name: 'Flansch.pdf', batch_id: 'b1', state: 'ready' },
  { id: 'b', file_name: 'Welle.pdf', batch_id: 'b1', state: 'in_review' },
  { id: 'c', file_name: 'Gehäuse.pdf', batch_id: 'b2', state: 'failed' },
];

test('filterDocuments narrows by batch, state and a case-insensitive name search', () => {
  assert.deepEqual(filterDocuments(DOCS, { batchId: 'b1' }).map((d) => d.id), ['a', 'b']);
  assert.deepEqual(filterDocuments(DOCS, { state: 'failed' }).map((d) => d.id), ['c']);
  assert.deepEqual(filterDocuments(DOCS, { search: 'flan' }).map((d) => d.id), ['a']);
  assert.deepEqual(filterDocuments(DOCS, { search: 'GEHÄUSE' }).map((d) => d.id), ['c']);
});

test('filterDocuments needsMe keeps only ready and in_review', () => {
  assert.deepEqual(filterDocuments(DOCS, { needsMe: true }).map((d) => d.id), ['a', 'b']);
});

test('filterDocuments with no filters returns everything, unchanged order', () => {
  assert.deepEqual(filterDocuments(DOCS, {}), DOCS);
});

test('formatEta renders hours and minutes, or just minutes under an hour', () => {
  assert.equal(formatEta(7800), '~2h 10m left');
  assert.equal(formatEta(125), '~2m left');
  assert.equal(formatEta(0), '~0m left');
});

test('formatEta is null-safe: no history yet says so, not a bogus number', () => {
  assert.equal(formatEta(null), 'estimating…');
  assert.equal(formatEta(undefined), 'estimating…');
});

test('formatQueueHeader covers idle, paused, and extracting with an estimate', () => {
  assert.equal(formatQueueHeader({ queued: 0, extracting: 0, paused: false, eta_seconds: null }),
    'Queue idle');
  assert.equal(formatQueueHeader({ queued: 4, extracting: 1, paused: true, eta_seconds: null }),
    'Paused · 4 queued');
  assert.equal(formatQueueHeader({ queued: 4, extracting: 1, paused: false, eta_seconds: 600 }),
    'Extracting · 4 queued · ~10m left');
});
