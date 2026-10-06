// The review journal (docs/plans/2026-10-07-hitl-review-grading-design.md §2):
// each state.js operation emits one self-contained event; an undo emits a
// retraction of the event it undid.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const S = await import('../../app/static/js/state.js');

function row(id, extra = {}) {
  return { id, pos: 0, char_type: 'Distance', nominal: '1', upper_tol: '',
           lower_tol: '', needs_review: true, review_reasons: [],
           target_region: [0, 0, 10, 10], balloon_xy: [1, 1], suggested: false, ...extra };
}
function load(rows) { S.setSession({ session_id: 's', image_url: '', rows, notes: null }); }
function capture() {
  const seen = [];
  const off = S.on('op', (e) => seen.push({ kind: e.kind, event: e.op.event() }));
  return { seen, off };
}

test('each operation describes itself after it ran', () => {
  load([row('a'), row('b'), row('s', { suggested: true })]);
  const { seen, off } = capture();
  S.apply(S.opEditCell('a', 'nominal', '2'));
  S.apply(S.opMoveRow('a', [3, 4]));
  S.apply(S.opDeleteRow('b'));
  S.apply(S.opAddRow(row('m', { source: 'manual' })));
  S.apply(S.opConfirmSuggestions(['s']));
  S.apply(S.opBulkReview(['a'], true));
  S.apply(S.opBulkReview(['a'], false));
  off();
  assert.deepEqual(seen.map((x) => x.event.type),
    ['edit_cell', 'move_row', 'delete_row', 'add_row', 'confirm_suggestions', 'accept', 'unaccept']);
  assert.deepEqual(seen[0].event, { type: 'edit_cell', id: 'a', field: 'nominal', old: '1', new: '2' });
  assert.deepEqual(seen[1].event, { type: 'move_row', id: 'a', xy: [3, 4] });
  assert.equal(seen[3].event.row.id, 'm');
});

test('undo and redo travel the bus as undo and do', () => {
  load([row('a')]);
  const { seen, off } = capture();
  S.apply(S.opEditCell('a', 'nominal', '2'));
  S.undo();
  S.redo();
  off();
  assert.deepEqual(seen.map((x) => x.kind), ['do', 'undo', 'do']);
});
