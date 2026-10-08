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

test('initWorkList is exported and callable with no DOM work-list elements present (no-op, no throw)', async () => {
  // A node import/smoke check, not a DOM test (the codebase has none): this
  // fails loudly if the export is renamed or dropped, and passes once the
  // module loads and the function exists and tolerates being called before
  // index.html's elements exist (e.g. from a future screen that imports it
  // eagerly). Guards against exactly the "renamed export, nobody noticed"
  // class of bug this plan's brief calls out.
  const { initWorkList } = await import('../../app/static/js/worklist.js');
  assert.equal(typeof initWorkList, 'function');
});

// A deletion is invisible to the since-cursor poll: GET /api/documents?since=
// only ever returns rows that CHANGED, and a removed row has no updated_at
// to be newer than anything, so it would never be noticed missing. Every
// 20th poll (~1 minute at the 3 s cadence) reconciles the whole list instead
// (decision 11); pure, so the cadence itself -- not the timer that drives
// it -- is what node checks.
test('shouldFullRefresh fires on every 20th poll, never the others', async () => {
  const { shouldFullRefresh } = await import('../../app/static/js/worklist.js');
  const fires = [];
  for (let i = 1; i <= 41; i++) if (shouldFullRefresh(i)) fires.push(i);
  assert.deepEqual(fires, [20, 40]);
});

test('shouldFullRefresh never fires on poll 0 (nothing has polled yet)', async () => {
  const { shouldFullRefresh } = await import('../../app/static/js/worklist.js');
  assert.equal(shouldFullRefresh(0), false);
});

// Review finding on 84168f4 (CRITICAL): deleteDocument is best-effort and
// resolves { ok: false, status } instead of throwing on a refusal (so Stop's
// own retry loop can tell "gone" from "hiccup" without a catch) -- a bulk
// loop that only counts THROWN errors silently treats a refused delete as a
// success. This stubs exactly what deleteDocument resolves on a 409 (the
// drawing is still extracting) and checks the tally reads it as a failure,
// with the fixed per-row message.
test('summarizeBulkFailures counts a resolved {ok:false} as a failure, not just a thrown one', async () => {
  const { summarizeBulkFailures } = await import('../../app/static/js/worklist.js');
  const results = [
    { ok: true, id: 'a' },                 // a normal success (e.g. moveToFront's JSON)
    { ok: false, status: 409 },            // deleteDocument's best-effort refusal: still extracting
    { ok: false, status: 0 },              // deleteDocument's best-effort refusal: no response at all
  ];
  const summary = summarizeBulkFailures(results);
  assert.equal(summary.count, 2);
  assert.match(summary.text, /still extracting — cancel it first/);
});

test('summarizeBulkFailures returns null when nothing failed', async () => {
  const { summarizeBulkFailures } = await import('../../app/static/js/worklist.js');
  assert.equal(summarizeBulkFailures([{ ok: true }, { id: 'x' }]), null);
});

test('summarizeBulkFailures groups failures by status with a fixed message per status', async () => {
  const { summarizeBulkFailures } = await import('../../app/static/js/worklist.js');
  const summary = summarizeBulkFailures([
    { ok: false, status: 409 }, { ok: false, status: 409 }, { ok: false, status: 500 },
  ]);
  assert.equal(summary.count, 3);
  assert.match(summary.text, /2 still extracting — cancel it first/);
  assert.match(summary.text, /1 failed \(500\)/);
});
