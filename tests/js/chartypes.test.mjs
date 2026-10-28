// The Characteristic column offers the closed char_type vocabulary but never
// forces it (operator feedback 2026-10-28): a reviewer can still type any
// text. suggest() is the pure half of that; table.js owns the DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { CHAR_TYPES, suggest } = await import('../../app/static/js/chartypes.js');

test('an untouched cell offers the whole vocabulary, whatever it currently holds', () => {
  // Focusing "Diameter" and seeing only "Diameter" would hide the very
  // alternatives the reviewer opened the list to find.
  assert.deepEqual(suggest('Diameter', 'Diameter'), CHAR_TYPES);
  assert.deepEqual(suggest('', ''), CHAR_TYPES);
});

test('typing narrows the list, case-insensitively, prefix matches first', () => {
  assert.deepEqual(suggest('dia', 'Distance'), ['Diameter']);
  // "ci": Circularity is a prefix match; Concentricity and Cylindricity only
  // contain it, and keep vocabulary order after the prefix match.
  assert.deepEqual(suggest('CI', 'Distance'), ['Circularity', 'Concentricity', 'Cylindricity']);
});

test('free text that matches nothing yields an empty list, never an error', () => {
  assert.deepEqual(suggest('Rundlauf', 'Distance'), []);
});

test('the vocabulary has no duplicates and starts with the common dimension types', () => {
  assert.equal(new Set(CHAR_TYPES).size, CHAR_TYPES.length);
  assert.deepEqual(CHAR_TYPES.slice(0, 3), ['Distance', 'Diameter', 'Radius']);
});

const { moveHighlight } = await import('../../app/static/js/chartypes.js');

test('arrow keys walk the list, wrap around, and -1 means "nothing highlighted"', () => {
  // Nothing highlighted: Enter must still commit the typed text, so the
  // menu starts with no highlight and only an arrow key arms it.
  assert.equal(moveHighlight(-1, +1, 3), 0);
  assert.equal(moveHighlight(-1, -1, 3), 2);
  assert.equal(moveHighlight(2, +1, 3), 0);
  assert.equal(moveHighlight(0, -1, 3), 2);
  assert.equal(moveHighlight(-1, +1, 0), -1);   // empty list: nothing to highlight
});
