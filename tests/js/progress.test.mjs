// Review progress (design §1). The old bar counted an unflagged row as
// reviewed (r.reviewed || !r.needs_review) -- exactly the rows nobody looked at.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const S = await import('../../app/static/js/state.js');

function row(id, extra = {}) {
  return { id, pos: 0, needs_review: false, suggested: false,
           target_region: [0, 0, 1, 1], ...extra };
}
function load(rows) { S.setSession({ session_id: 's', image_url: '', rows, notes: null }); }

test('progress counts flagged balloons as the queue and unflagged ones as unchecked', () => {
  load([row('f1', { needs_review: true }), row('f2', { needs_review: true }),
        row('u1'), row('u2'), row('s', { suggested: true, needs_review: true })]);
  assert.deepEqual(S.progress(), { resolved: 0, total: 2, outstanding: 2, unchecked: 2 });
  S.apply(S.opBulkReview(['f1'], true));
  assert.deepEqual(S.progress(), { resolved: 1, total: 2, outstanding: 1, unchecked: 2 });
});

test('accepting an unflagged row does not make it checked', () => {
  load([row('u1')]);
  S.apply(S.opBulkReview(['u1'], true));
  assert.equal(S.progress().unchecked, 1);
});

test('Finish is allowed only when no flagged balloon is outstanding', () => {
  load([row('f1', { needs_review: true }), row('s', { suggested: true, needs_review: true })]);
  assert.equal(S.canFinish(), false);
  S.apply(S.opDeleteRow('f1'));
  assert.equal(S.canFinish(), true);   // the unconfirmed suggestion does not block
});
