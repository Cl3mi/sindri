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

// startBlocker: the client-side mirror of the server's own Start refusals
// (an unfinished upload, nothing included) plus the out-of-scope guard,
// which the server already enforces (decision 26) but costs nothing to
// check here too -- it can never actually fire via the UI, since ticking
// Include on an out-of-scope row is what STORES the permission, but a
// draft touched by another tab's raw API call is not impossible.
test('startBlocker refuses while an upload is unfinished, regardless of documents', () => {
  const { startBlocker } = mod;
  const view = {
    uploads: [{ id: 'u1', state: 'receiving' }],
    documents: [{ id: 'd1', included: true, out_of_scope: false, allowed_out_of_scope: false }],
  };
  assert.match(startBlocker(view), /upload/i);
});

test('startBlocker refuses when nothing is included', () => {
  const { startBlocker } = mod;
  const view = {
    uploads: [{ id: 'u1', state: 'complete' }],
    documents: [{ id: 'd1', included: false, out_of_scope: false, allowed_out_of_scope: false }],
  };
  assert.match(startBlocker(view), /included/i);
});

test('startBlocker refuses an included out-of-scope row with no stored permission', () => {
  const { startBlocker } = mod;
  const view = {
    uploads: [],
    documents: [{ id: 'd1', included: true, out_of_scope: true, allowed_out_of_scope: false }],
  };
  assert.match(startBlocker(view), /out.of.scope/i);
});

test('startBlocker is null (startable) once an upload is complete and something is included', () => {
  const { startBlocker } = mod;
  const view = {
    uploads: [{ id: 'u1', state: 'complete' }],
    documents: [{ id: 'd1', included: true, out_of_scope: false, allowed_out_of_scope: false }],
  };
  assert.equal(startBlocker(view), null);
});

test('startBlocker treats a missing/empty view as not startable, not a crash', () => {
  const { startBlocker } = mod;
  assert.notEqual(startBlocker(null), null);
  assert.notEqual(startBlocker({ uploads: [], documents: [] }), null);
});

// archiveEmptyMessage: told apart from "still uploading" (the pending-uploads
// list already explains that) and from "everything ignored" (carried from
// B2) -- the exact empty-archive wording the carry-note (CLAUDE.md B3 note)
// asks for only when nothing was even ignored.
test('archiveEmptyMessage is silent while documents exist', () => {
  const { archiveEmptyMessage } = mod;
  assert.equal(archiveEmptyMessage({ documents: [{ id: 'd1' }], uploads: [] }), '');
});

test('archiveEmptyMessage is silent while an upload is still unfinished', () => {
  const { archiveEmptyMessage } = mod;
  const view = { documents: [], uploads: [{ id: 'u1', state: 'receiving', ignored_total: 0 }] };
  assert.equal(archiveEmptyMessage(view), '');
});

test('archiveEmptyMessage says the archive held no PDF files when nothing was ignored either', () => {
  const { archiveEmptyMessage } = mod;
  const view = { documents: [], uploads: [{ id: 'u1', state: 'complete', ignored_total: 0 }] };
  assert.equal(archiveEmptyMessage(view), 'The archive held no PDF files.');
});

test('archiveEmptyMessage says files were ignored when a complete upload ignored entries', () => {
  const { archiveEmptyMessage } = mod;
  const view = { documents: [], uploads: [{ id: 'u1', state: 'complete', ignored_total: 3 }] };
  assert.equal(archiveEmptyMessage(view),
    'This import holds no readable PDF — every file was ignored or none was a PDF.');
});
