// Review UX round, task 10: the work list reads like the workbench table --
// the same row states (left bar: warn = needs you, ok = done, err = failed)
// and a preview pane beside it. Pure rules only; the DOM is checked in the
// headless-browser pass (plan task 11).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { rowTone, previewFacts } = await import('../../app/static/js/worklist.js');

test('rowTone maps a drawing state onto the workbench row states', () => {
  assert.equal(rowTone({ state: 'ready' }), 'review');       // needs me, like a flagged row
  assert.equal(rowTone({ state: 'in_review' }), 'review');
  assert.equal(rowTone({ state: 'finished' }), 'reviewed');
  assert.equal(rowTone({ state: 'failed' }), 'failed');
  assert.equal(rowTone({ state: 'extracting' }), 'busy');
  assert.equal(rowTone({ state: 'queued' }), 'busy');
  assert.equal(rowTone({ state: 'staged' }), '');
  assert.equal(rowTone({ state: 'purged' }), 'inert');
});

test('previewFacts shows a thumbnail only when the extraction left an image', () => {
  const base = { id: 'abc', file_name: 'Welle', batch_name: 'pilot', flagged_open: 2,
                 flagged_total: 5, unchecked: 7 };
  for (const state of ['ready', 'in_review', 'finished']) {
    assert.equal(previewFacts({ ...base, state }).imageUrl, '/api/image/abc');
  }
  for (const state of ['staged', 'queued', 'extracting', 'failed', 'cancelled', 'purged']) {
    assert.equal(previewFacts({ ...base, state }).imageUrl, null);
  }
  const f = previewFacts({ ...base, state: 'in_review' });
  assert.equal(f.title, 'Welle');
  assert.deepEqual(f.facts, [['Flagged', '2 of 5 open'], ['Unchecked', '7'], ['Batch', 'pilot']]);
  assert.deepEqual(previewFacts({ ...base, state: 'ready', flagged_open: null, unchecked: null,
                                  flagged_total: null, batch_name: '' }).facts, []);
});
