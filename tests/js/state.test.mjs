// The reviewer UI's suggestion rules, tested in node (no DOM needed):
// docs/plans/2026-10-07-suggestion-tray-design.md §4.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const S = await import('../../app/static/js/state.js');

function load(rows) {
  S.setSession({ session_id: 's', image_url: '', rows, notes: null });
}

function row(id, x, y, extra = {}) {
  return { id, pos: 0, char_type: 'Distance', nominal: '1', upper_tol: '',
           lower_tol: '', needs_review: false, review_reasons: [],
           target_region: [x, y, x + 10, y + 10], suggested: false, ...extra };
}

test('renumber numbers balloons only; suggestions keep 0', () => {
  load([row('a', 0, 0), row('s', 50, 0, { suggested: true }), row('b', 100, 0)]);
  S.renumber();
  const pos = Object.fromEntries(S.state.rows.map((r) => [r.id, r.pos]));
  assert.deepEqual(pos, { a: 1, s: 0, b: 2 });
});

test('the Low-confidence filter shows suggestions only; Review and OK hide them', () => {
  load([row('a', 0, 0), row('r', 30, 0, { needs_review: true }),
        row('s', 60, 0, { suggested: true })]);
  const visible = (f) => { S.state.filter = f;
    return S.state.rows.filter(S.isVisibleRow).map((r) => r.id).sort(); };
  assert.deepEqual(visible('suggested'), ['s']);
  assert.deepEqual(visible('review'), ['r']);
  assert.deepEqual(visible('ok'), ['a']);
  assert.deepEqual(visible('all'), ['a', 'r', 's']);
  S.state.filter = 'all';
});

test('counts separate suggestions from review and ok', () => {
  load([row('a', 0, 0), row('r', 30, 0, { needs_review: true }),
        row('s', 60, 0, { suggested: true })]);
  assert.deepEqual(S.counts(), { all: 3, review: 1, ok: 1, suggested: 1 });
});

test('confirming a suggestion makes it a numbered, reviewed balloon; undo restores it', () => {
  load([row('a', 0, 0), row('s', 50, 0, { suggested: true }), row('b', 100, 0)]);
  S.renumber();
  S.apply(S.opConfirmSuggestions(['s']));
  let s = S.state.rows.find((r) => r.id === 's');
  assert.equal(s.suggested, false);
  assert.equal(s.reviewed, true);
  assert.deepEqual(S.state.rows.map((r) => r.pos), [1, 2, 3]);
  S.undo();
  s = S.state.rows.find((r) => r.id === 's');
  assert.equal(s.suggested, true);
  assert.equal(s.reviewed, false);
  assert.equal(s.pos, 0);
});

test('confirm ignores rows that are not suggestions', () => {
  load([row('a', 0, 0)]);
  S.apply(S.opConfirmSuggestions(['a']));
  assert.equal(S.state.rows[0].reviewed, false);
});
