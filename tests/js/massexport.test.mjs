// Mass export (batch design §4): pure status text, cadence, URLs and error
// copy for the work list's "Export selected" bulk action. DOM wiring
// (requestExport/getExport polling, the button's disabled state, the
// triggered download) lives in worklist.js, checked by import only -- see
// tests/js/worklist.test.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { exportStatusText, pollIntervalMs, downloadUrl, exportFailureMessage,
         downloadFailureMessage, shouldContinuePolling } from '../../app/static/js/massexport.js';

test('exportStatusText describes each job state', () => {
  assert.equal(exportStatusText({ state: 'running', done: 2, total: 5 }), 'Exporting 2 / 5…');
  assert.equal(exportStatusText({ state: 'done', done: 5, total: 5 }), 'Export ready');
  assert.equal(exportStatusText({ state: 'failed' }), 'Export failed');
});

test('pollIntervalMs matches the work list\'s own poll cadence', () => {
  assert.equal(pollIntervalMs(), 3000);
});

test('downloadUrl points at the job\'s download route', () => {
  assert.equal(downloadUrl('abc123'), '/api/exports/abc123/download');
});

// Only a fixed, known tag (app/batch/exports.py's own "OutOfDisk") earns its
// own sentence -- everything else (an arbitrary exception class name, or no
// error_type at all) falls back to the generic one, never leaking a class
// name to the reviewer.
test('exportFailureMessage names disk exhaustion; anything else is generic', () => {
  assert.equal(exportFailureMessage('OutOfDisk'), 'Export failed — not enough disk space.');
  assert.equal(exportFailureMessage('RuntimeError'), 'Export failed — try again.');
  assert.equal(exportFailureMessage(undefined), 'Export failed — try again.');
  assert.equal(exportFailureMessage(null), 'Export failed — try again.');
});

// The ZIP's 24h cleanup sweep (Task 7) can expire a job between "done" and
// the reviewer clicking download; app/main.py's download route answers 410
// for exactly that case, distinct from every other failure.
test('downloadFailureMessage names expiry; anything else is generic', () => {
  assert.equal(downloadFailureMessage(410), 'export expired — export again');
  assert.equal(downloadFailureMessage(409), 'Export failed — try again.');
  assert.equal(downloadFailureMessage(500), 'Export failed — try again.');
});

// The bulk-export button must never stay disabled forever: a poll that
// throws (network error, a malformed reply) passes no job at all, and that
// must read the same as a terminal state, not as "still running, keep
// trying silently".
test('shouldContinuePolling keeps going only for a running job; a thrown poll (no job) re-enables', () => {
  assert.equal(shouldContinuePolling({ state: 'running', done: 1, total: 5 }), true);
  assert.equal(shouldContinuePolling({ state: 'done' }), false);
  assert.equal(shouldContinuePolling({ state: 'failed' }), false);
  assert.equal(shouldContinuePolling(null), false);
  assert.equal(shouldContinuePolling(undefined), false);
});
