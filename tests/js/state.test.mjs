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

test('opBulkReview de-duplicates ids so undo restores the original value, not the applied one', () => {
  // A duplicate id made the op's own prev-map overwrite the captured original
  // with the value JUST applied, so undo "restored" the new value -- making
  // the op unrestorable and a healthy session's replay diverge from export.
  load([row('a', 0, 0, { needs_review: true })]);
  S.apply(S.opBulkReview(['a', 'a'], true));
  S.undo();
  assert.equal(S.state.rows.find((r) => r.id === 'a').reviewed, false);
});

test('opConfirmSuggestions de-duplicates ids in its own logged event', () => {
  load([row('s', 0, 0, { suggested: true })]);
  const op = S.opConfirmSuggestions(['s', 's']);
  S.apply(op);
  assert.deepEqual(op.event().ids, ['s']);
});

// The stale-tab banner (banner.js) is read-only: another tab holds the
// writer, so a further apply()/undo()/redo() here would only be refused the
// same way the next flush is -- and every edit path (table, viewer, the
// keyboard shortcuts) ultimately calls one of these three, so gating them
// here is the single chokepoint that blocks all of them at once.
test('apply() is a no-op while readOnly: no mutation, no op emitted', () => {
  load([row('a', 0, 0)]);
  S.state.readOnly = true;
  let opEmitted = false;
  const off = S.on('op', () => { opEmitted = true; });
  S.apply(S.opDeleteRow('a'));
  off();
  S.state.readOnly = false;
  assert.equal(S.state.rows.length, 1);
  assert.equal(opEmitted, false);
});

test('undo()/redo() are no-ops while readOnly', () => {
  load([row('a', 0, 0)]);
  S.apply(S.opDeleteRow('a'));
  assert.equal(S.state.rows.length, 0);
  S.state.readOnly = true;
  S.undo();
  assert.equal(S.state.rows.length, 0);   // still deleted: undo was refused
  S.state.readOnly = false;
  S.undo();
  assert.equal(S.state.rows.length, 1);   // works again once read-only lifts
  S.state.readOnly = true;
  S.redo();
  assert.equal(S.state.rows.length, 1);   // still restored: redo was refused
  S.state.readOnly = false;
});

test('setSession() and clearSession() reset readOnly: a fresh or closed drawing is never stuck read-only', () => {
  S.state.readOnly = true;
  load([row('a', 0, 0)]);
  assert.equal(S.state.readOnly, false);
  S.state.readOnly = true;
  S.clearSession();
  assert.equal(S.state.readOnly, false);
});

// Campaign C1 (plan Task 8): the journal's move_target / set_note / reasons,
// mirrored op for op so a restored review rebuilds what replay.py seals.
test('opMoveTarget moves the anchor, undoes, and emits move_target', () => {
  load([row('a', 0, 0)]);
  const op = S.opMoveTarget('a', [5, 5, 20, 20]);
  S.apply(op);
  assert.deepEqual(S.state.rows[0].target_region, [5, 5, 20, 20]);
  assert.deepEqual(op.event(), { type: 'move_target', id: 'a', box: [5, 5, 20, 20] });
  S.undo();
  assert.deepEqual(S.state.rows[0].target_region, [0, 0, 10, 10]);
});

test('opSetNote sets the drawing note and undo restores the old one', () => {
  load([row('a', 0, 0)]);
  assert.equal(S.state.note, '');
  const op = S.opSetNote('we never balloon chamfers');
  S.apply(op);
  assert.equal(S.state.note, 'we never balloon chamfers');
  assert.deepEqual(op.event(), { type: 'set_note', text: 'we never balloon chamfers' });
  S.undo();
  assert.equal(S.state.note, '');
});

test('a reason rides on edit_cell and delete_row only when given', () => {
  load([row('a', 0, 0)]);
  assert.equal('reason' in S.opEditCell('a', 'nominal', '2').event(), false);
  const withReason = S.opEditCell('a', 'nominal', '2', 'misread_digit');
  S.apply(withReason);
  assert.equal(withReason.event().reason, 'misread_digit');
  assert.equal('reason' in S.opDeleteRow('a').event(), false);
  assert.equal(S.opDeleteRow('a', 'duplicate').event().reason, 'duplicate');
});

test('opFromEvent restores move_target, set_note and reasons', () => {
  load([row('a', 0, 0)]);
  S.restoreSession({ session_id: 's', image_url: '', rows: [row('a', 0, 0)], notes: null }, [
    { seq: 1, type: 'move_target', id: 'a', box: [1, 1, 9, 9] },
    { seq: 2, type: 'set_note', text: 'n' },
    { seq: 3, type: 'edit_cell', id: 'a', field: 'nominal', old: '1', new: '3', reason: 'other' },
  ]);
  assert.deepEqual(S.state.rows[0].target_region, [1, 1, 9, 9]);
  assert.equal(S.state.note, 'n');
  assert.equal(S.state.rows[0].nominal, '3');
});

test('opFlagRows moves rows to Review as a manual flag and undo restores them exactly', () => {
  load([row('a', 0, 0),
        row('b', 50, 0, { needs_review: true, review_reasons: ['no_tolerance'] }),
        row('s', 90, 0, { suggested: true })]);
  S.apply(S.opBulkReview(['a', 'b'], true));   // setSession resets reviewed; accept for real
  const op = S.opFlagRows(['a', 'b', 's', 'a', 'ghost']);
  S.apply(op);
  const get = (id) => S.state.rows.find((r) => r.id === id);
  assert.equal(get('a').needs_review, true);
  assert.equal(get('a').reviewed, false);
  assert.deepEqual(get('a').review_reasons, ['manual']);
  assert.deepEqual(get('b').review_reasons, ['no_tolerance', 'manual']);
  assert.equal(get('s').needs_review, false);          // a suggestion is not a balloon
  assert.deepEqual(op.event(), { type: 'flag', ids: ['a', 'b', 's', 'ghost'] });
  assert.equal(S.progress().outstanding, 2);          // blocks Approve until resolved
  S.undo();                                            // undoes the flag, not the accept
  assert.equal(get('a').needs_review, false);
  assert.equal(get('a').reviewed, true);
  assert.deepEqual(get('a').review_reasons, []);
  assert.deepEqual(get('b').review_reasons, ['no_tolerance']);
  assert.equal(get('b').reviewed, true);
});

test('a flag event rebuilds the same op after a reload', () => {
  load([row('a', 0, 0)]);
  const op = S.opFromEvent({ type: 'flag', ids: ['a'] });
  S.apply(op);
  assert.equal(S.state.rows[0].needs_review, true);
});

// Review UX round, task 7: a freshly scanned row is selected so the reviewer
// sees what was added, but that selection used to stay forever and drew the
// anchor box and handles around it. Only a click on the balloon asks for the
// handles, and an empty-canvas click or Esc ends the selection.
test('clearSelection drops the selection and the anchor-handle request', () => {
  load([row('a', 0, 0)]);
  S.state.selectedId = 'a';
  S.state.anchorId = 'a';
  let selected = 'unset';
  const off = S.on('select', (id) => { selected = id; });
  S.clearSelection();
  off();
  assert.equal(S.state.selectedId, null);
  assert.equal(S.state.anchorId, null);
  assert.equal(selected, null);
});

test('an added row is selected but does not request anchor handles', () => {
  load([row('a', 0, 0)]);
  S.apply(S.opAddRow(row('n', 50, 0)));
  assert.equal(S.state.selectedId, 'n');
  assert.equal(S.showAnchorFor('n'), false);
  S.state.anchorId = 'n';
  assert.equal(S.showAnchorFor('n'), true);
  S.clearSelection();
  assert.equal(S.showAnchorFor('n'), false);
});

test('a loaded session starts with no anchor request', () => {
  S.state.anchorId = 'x';
  load([row('a', 0, 0)]);
  assert.equal(S.state.anchorId, null);
});

// Review UX round, task 8: the undo and redo buttons say what they will do.
test('undoLabel and redoLabel name the operation and its balloon number', () => {
  load([row('a', 0, 0), row('b', 100, 0), row('c', 200, 0)]);
  S.renumber();
  assert.equal(S.undoLabel(), null);
  assert.equal(S.redoLabel(), null);
  S.apply(S.opDeleteRow('b'));
  assert.equal(S.undoLabel(), 'delete balloon 2');   // the row is gone, its number is not
  S.undo();
  assert.equal(S.undoLabel(), null);
  assert.equal(S.redoLabel(), 'delete balloon 2');
  S.redo();
  S.apply(S.opEditCell('c', 'nominal', '9'));
  assert.equal(S.undoLabel(), 'edit nominal on balloon 2');   // c became number 2
  S.apply(S.opMoveRow('a', [5, 5]));
  assert.equal(S.undoLabel(), 'move balloon 1');
});

test('undoLabel covers bulk and add operations', () => {
  load([row('a', 0, 0), row('b', 100, 0)]);
  S.renumber();
  S.apply(S.opFlagRows(['a', 'b']));
  assert.equal(S.undoLabel(), 'move 2 rows to review');
  S.apply(S.opBulkReview(['a'], true));
  assert.equal(S.undoLabel(), 'accept 1 row');
  S.apply(S.opAddRow(row('n', 300, 0)));
  assert.equal(S.undoLabel(), 'add balloon 3');
});
