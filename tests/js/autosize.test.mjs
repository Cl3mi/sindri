// The per-drawing note box grows with its text (operator feedback 2026-10-28:
// "the whole text should show, not a truncated single line"). fitHeight is the
// pure rule; autosize() binds it to a textarea.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { fitHeight } = await import('../../app/static/js/autosize.js');

test('the box is as tall as its content up to the cap, and no scrollbar until then', () => {
  assert.deepEqual(fitHeight(40, 160, 28), { height: 40, scroll: false });
  assert.deepEqual(fitHeight(160, 160, 28), { height: 160, scroll: false });
});

test('past the cap the height stops growing and the box scrolls instead', () => {
  assert.deepEqual(fitHeight(400, 160, 28), { height: 160, scroll: true });
});

test('an empty or tiny box keeps a one-line minimum so it never collapses', () => {
  assert.deepEqual(fitHeight(0, 160, 28), { height: 28, scroll: false });
  assert.deepEqual(fitHeight(10, 160, 28), { height: 28, scroll: false });
});
