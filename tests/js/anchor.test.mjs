import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  dragBox, settleBox, beyondDeadZone, MIN_SIDE, DRAG_DEAD_ZONE_PX,
} from '../../app/static/js/anchor.js';

const B = [10, 10, 50, 40];

test('the body handle moves the whole box', () => {
  assert.deepEqual(dragBox(B, 'body', 5, -3), [15, 7, 55, 37]);
});

test('a corner moves only its two edges', () => {
  assert.deepEqual(dragBox(B, 'nw', -4, -2), [6, 8, 50, 40]);
  assert.deepEqual(dragBox(B, 'se', 10, 10), [10, 10, 60, 50]);
  assert.deepEqual(dragBox(B, 'ne', 3, 4), [10, 14, 53, 40]);
  assert.deepEqual(dragBox(B, 'sw', 3, 4), [13, 10, 50, 44]);
});

// The journal refuses an empty or inverted move_target box and a refused
// event stalls the reviewer's whole flush, so a drag past the opposite edge
// must clamp, never cross it.
test('a box never collapses or inverts', () => {
  const b = dragBox(B, 'se', -100, -100);
  assert.ok(b[2] - b[0] >= MIN_SIDE && b[3] - b[1] >= MIN_SIDE, String(b));
  const c = dragBox(B, 'nw', 100, 100);
  assert.ok(c[0] < c[2] && c[1] < c[3], String(c));
});

// settleBox is the last gate before a move_target reaches the journal, which
// refuses anything that is not four finite, ordered numbers.
test('a settled box is rounded to 0.1 and anything unjournalable is null', () => {
  assert.deepEqual(settleBox([10.04, 9.96, 50.15, 40.01]), [10, 10, 50.2, 40]);
  assert.equal(settleBox([NaN, 0, 10, 10]), null);
  assert.equal(settleBox([0, 0, Infinity, 10]), null);
  assert.equal(settleBox([10, 0, 10.01, 10]), null);   // collapses when rounded
  assert.equal(settleBox([0, 20, 10, 10]), null);
});

// At fit-page zoom one screen pixel is several plan pixels, so a trembling
// click would journal a move the grading reads as a correction. The dead zone
// is in SCREEN pixels for that reason: intent is a hand movement, not a
// distance on the drawing.
test('a drag counts only once it leaves the screen-space dead zone', () => {
  const s = { x: 100, y: 100 };
  assert.equal(beyondDeadZone(s, { x: 100, y: 100 }), false);
  assert.equal(beyondDeadZone(s, { x: 102, y: 101 }), false);
  assert.equal(beyondDeadZone(s, { x: 100 + DRAG_DEAD_ZONE_PX, y: 100 }), true);
  assert.equal(beyondDeadZone(s, { x: 97, y: 96 }), true);
  assert.equal(beyondDeadZone(s, { x: 105, y: 100 }, 10), false);
});

// A detector box can already be thinner than MIN_SIDE; dragging one corner
// must not silently widen the axis the reviewer did not touch.
test('a sub-MIN_SIDE box is not inflated on an axis the drag left alone', () => {
  const thin = [10, 10, 50, 12];            // 2 px tall
  assert.deepEqual(dragBox(thin, 'se', 5, 0), [10, 10, 55, 12]);
  assert.deepEqual(dragBox(thin, 'nw', -5, 0), [5, 10, 50, 12]);
  // ...but it still never inverts when the drag does cross it
  const c = dragBox(thin, 'se', 0, -100);
  assert.ok(c[1] < c[3], String(c));
});
