import { test } from 'node:test';
import assert from 'node:assert/strict';
import { approvedCount, finishCopy, isCampaignDoc, noteToCommit, NOTE_MAX } from '../../app/static/js/campaign.js';

const d = (id, batch, extra = {}) => ({ id, batch_id: batch, campaign: null,
  state: 'ready', final_revision: null, ...extra });

test('approved = drawings of the batch with a sealed revision', () => {
  const docs = [d('a', 'B', { final_revision: 1, state: 'finished' }),
                d('b', 'B', { final_revision: 2, state: 'in_review' }),  // re-opened: still approved once
                d('c', 'B'), d('x', 'Z', { final_revision: 1 })];
  assert.deepEqual(approvedCount(docs, 'B'), { approved: 2, total: 3 });
});

test('campaign drawings get Approve copy, ordinary ones keep Finish & Export', () => {
  assert.equal(isCampaignDoc(d('a', 'B', { campaign: 'pilot-1' })), true);
  assert.equal(isCampaignDoc(d('a', 'B')), false);
  assert.equal(finishCopy(true).label, 'Approve drawing');
  assert.match(finishCopy(true).title, /no export/i);
  assert.equal(finishCopy(false).label, 'Finish & Export');
});

test('a note commits only onto the drawing it was typed on', () => {
  // Typed on A, then Back/Forward moved the session to B with focus kept:
  // the text is A's, so it must never be journaled onto B.
  assert.equal(noteToCommit('A', 'B', 'we never balloon chamfers', ''), null);
  assert.equal(noteToCommit('A', 'A', 'we never balloon chamfers', ''), 'we never balloon chamfers');
  assert.equal(noteToCommit(null, 'A', 'x', ''), null);      // never focused: nothing typed
  assert.equal(noteToCommit('A', null, 'x', ''), null);      // drawing closed
});

test('an unchanged note is not journaled again, and an over-long one is cut', () => {
  assert.equal(noteToCommit('A', 'A', 'same', 'same'), null);
  assert.equal(noteToCommit('A', 'A', '', 'old'), '');       // clearing the note is a change
  const long = 'x'.repeat(NOTE_MAX + 5);
  assert.equal(noteToCommit('A', 'A', long, '').length, NOTE_MAX);
  assert.equal(noteToCommit('A', 'A', long, 'x'.repeat(NOTE_MAX)), null);
});
