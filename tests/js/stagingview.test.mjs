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

// removalMessage: deleteDocument/deleteUpload (api.js) are best-effort and
// never throw, so a `try/catch` around either call can never see a refusal
// -- this is the only place that can. { ok: true } and a thrown-style
// non-result both mean "nothing to say" (the removal worked, or there was
// never a call to judge).
test('removalMessage is silent on success or a missing result', () => {
  const { removalMessage } = mod;
  assert.equal(removalMessage({ ok: true, status: 200 }), null);
  assert.equal(removalMessage(undefined), null);
  assert.equal(removalMessage(null), null);
});

test('removalMessage treats a 409 as "the import changed", not a generic failure', () => {
  const { removalMessage } = mod;
  const msg = removalMessage({ ok: false, status: 409 });
  assert.equal(msg.kind, 'warn');
  assert.equal(msg.msg, 'The import changed — review it and try again.');
});

test('removalMessage reports any other failure as a plain could-not-remove', () => {
  const { removalMessage } = mod;
  const msg = removalMessage({ ok: false, status: 0 });
  assert.equal(msg.kind, 'error');
  assert.match(msg.msg, /could not remove it/i);
});

// startConflictCopy: Start's two distinct 409s (app/batch/repo.py)
// distinguished by the server's own FIXED sentence (CLAUDE.md §4 -- a
// string compared exactly, not a guess from the status code alone).
test('startConflictCopy recognises the already-started detail and does not call it "changed"', () => {
  const { startConflictCopy } = mod;
  const copy = startConflictCopy('this import has already started');
  assert.equal(copy.title, 'Already started');
  assert.doesNotMatch(copy.msg, /changed/i);
});

test('startConflictCopy defaults to the "import changed" copy for any other detail', () => {
  const { startConflictCopy } = mod;
  assert.equal(startConflictCopy('the import changed — reload it').title, 'The import changed');
  assert.equal(startConflictCopy(undefined).title, 'The import changed');
});

// orphanedUploadIds: the only identity importFiles exposes to a caller that
// never sees an upload's id before it completes is the file's own name and
// size (same identity importone.js's inspectDraft matches a resumed draft
// by) -- matched only among uploads the server has not already completed,
// and only for files this run's result marked as CANCELLED, not merely
// failed for their own reason.
test('orphanedUploadIds matches a cancelled file to its incomplete upload by name and size', () => {
  const { orphanedUploadIds } = mod;
  const uploads = [
    { id: 'u1', name: 'a.pdf', size: 100, state: 'receiving' },
    { id: 'u2', name: 'b.pdf', size: 200, state: 'complete' },
  ];
  const cancelled = [{ name: 'a.pdf', size: 100 }];
  assert.deepEqual(orphanedUploadIds(uploads, cancelled), ['u1']);
});

test('orphanedUploadIds never matches an upload the server already completed', () => {
  const { orphanedUploadIds } = mod;
  const uploads = [{ id: 'u2', name: 'b.pdf', size: 200, state: 'complete' }];
  assert.deepEqual(orphanedUploadIds(uploads, [{ name: 'b.pdf', size: 200 }]), []);
});

test('orphanedUploadIds is empty with nothing cancelled or nothing incomplete', () => {
  const { orphanedUploadIds } = mod;
  assert.deepEqual(orphanedUploadIds([], []), []);
  assert.deepEqual(orphanedUploadIds(undefined, undefined), []);
});

// pendingItems: the merge #sv-pending renders from -- server-known
// incomplete uploads plus local in-flight ones an Add-files run in this tab
// has not yet been confirmed by a reload, told apart by `local` so the row
// builder knows whether to offer Remove (DELETE) or Cancel (abort).
test('pendingItems lists server-incomplete uploads, never a complete one', () => {
  const { pendingItems } = mod;
  const uploads = [
    { id: 'u1', name: 'a.pdf', size: 1, state: 'receiving', received: 0 },
    { id: 'u2', name: 'b.pdf', size: 2, state: 'complete', received: 2 },
  ];
  const items = pendingItems(uploads, []);
  assert.deepEqual(items.map((i) => i.id), ['u1']);
  assert.equal(items[0].local, false);
});

test('pendingItems appends local in-flight entries not yet done', () => {
  const { pendingItems } = mod;
  const addProgress = [
    { name: 'c.pdf', sent: 10, total: 100, done: false },
    { name: 'd.pdf', sent: 100, total: 100, done: true },
  ];
  const items = pendingItems([], addProgress);
  assert.equal(items.length, 1);
  assert.equal(items[0].local, true);
  assert.equal(items[0].name, 'c.pdf');
  assert.equal(items[0].received, 10);
});
