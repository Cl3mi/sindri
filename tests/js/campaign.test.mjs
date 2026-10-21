import { test } from 'node:test';
import assert from 'node:assert/strict';
import { approvedCount, finishCopy, isCampaignDoc } from '../../app/static/js/campaign.js';

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
