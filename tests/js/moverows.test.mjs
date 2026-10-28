// Move to OK / Move to Review (review UX plan task 3): which rows each button
// can act on, and what the per-row action does. Pure, so no DOM is needed.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { moveTargets, rowAction, targetIds } = await import('../../app/static/js/moverows.js');

const rows = [
  { id: 'q', needs_review: true, reviewed: false },                 // in the Review queue
  { id: 'qa', needs_review: true, reviewed: true },                  // flagged, accepted: OK
  { id: 'ok', needs_review: false, reviewed: false },                // unflagged: OK
  { id: 's', suggested: true, needs_review: false, reviewed: false }, // tray: neither
];

test('Move to OK takes only rows still in the Review queue', () => {
  assert.deepEqual(moveTargets(rows, ['q', 'qa', 'ok', 's', 'ghost']).toOk, ['q']);
});

test('Move to Review takes every balloon that is currently OK, never a suggestion', () => {
  assert.deepEqual(moveTargets(rows, ['q', 'qa', 'ok', 's']).toReview, ['qa', 'ok']);
});

test('buttons act on the ticked rows, else on the selected row, else nothing', () => {
  assert.deepEqual(targetIds(['a', 'b'], 'c'), ['a', 'b']);
  assert.deepEqual(targetIds([], 'c'), ['c']);
  assert.deepEqual(targetIds([], null), []);
});

test('the per-row action flips the row to the other side', () => {
  assert.deepEqual(rowAction(rows[0]), { kind: 'ok', label: 'Move to OK' });
  assert.deepEqual(rowAction(rows[1]), { kind: 'review', label: 'Move to Review' });
  assert.deepEqual(rowAction(rows[2]), { kind: 'review', label: 'Move to Review' });
  assert.equal(rowAction(rows[3]), null);                           // a suggestion has none
});
