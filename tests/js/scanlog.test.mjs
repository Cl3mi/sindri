// What the viewer logs for a scan-rectangle attempt (review UX plan task 4).
// The server refuses a malformed event and a refusal stalls the reviewer's
// whole journal, so the event must be conformant whatever the OCR returned.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { scanEvent, TEXT_MAX } = await import('../../app/static/js/scanlog.js');

test('a kept read carries the row id, the box, the text and the timing', () => {
  const e = scanEvent({ box: [10.4, 20, 110, 60], ms: 412.7,
    row: { id: 'r1', raw_text: '12 +0.1', nominal: '12', confidence: 0.93 } });
  assert.deepEqual(e, { type: 'scan_read', row_id: 'r1', box: [10.4, 20, 110, 60],
    text: '12 +0.1', conf: 0.93, ms: 413, empty: false });
});

test('an empty read is an attempt that missed, and still names its row', () => {
  const e = scanEvent({ box: [0, 0, 5, 5], ms: 9, row: { id: 'r2', raw_text: '  ', nominal: '', confidence: 0 } });
  assert.equal(e.empty, true);
  assert.equal(e.text, '');
  assert.equal(e.row_id, 'r2');
});

test('a failed request has no row and says so', () => {
  const e = scanEvent({ box: [0, 0, 5, 5], ms: 9, failed: true });
  assert.deepEqual(e, { type: 'scan_read', row_id: null, box: [0, 0, 5, 5], text: '',
    conf: null, ms: 9, empty: true, failed: true });
});

test('text is clamped and non-finite numbers become null/0 so the server never refuses it', () => {
  const e = scanEvent({ box: [0, 0, 5, 5], ms: NaN,
    row: { id: 'r', raw_text: 'x'.repeat(TEXT_MAX + 50), nominal: '1', confidence: NaN } });
  assert.equal(e.text.length, TEXT_MAX);
  assert.equal(e.conf, null);
  assert.equal(e.ms, 0);
});
