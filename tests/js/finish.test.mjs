// Finish & Export (docs/plans/2026-10-07-hitl-review-grading-design.md §2):
// one review pass must seal exactly one revision. Grading reads sealed
// revisions, so a double click or a retry after a failed export must never
// mint a second sealed/rN.json for the same journal.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { createFinisher } = await import('../../app/static/js/finish.js');

// A fake journal: `lastSeq` is what the UI recorded, `ackTo` what flush()
// reports the server holds.
function fakeJournal({ active = true, lastSeq = 3, ackTo = null } = {}) {
  const j = {
    active, writer: active ? 'w' : null, lastSeq,
    ackTo,
    flushes: 0,
    async flush() { j.flushes++; return j.ackTo ?? j.lastSeq; },
  };
  return j;
}

function harness({ journal = fakeJournal(), canFinish = () => true,
                   exportFails = 0, sealGate = null } = {}) {
  const calls = { seals: [], exports: 0 };
  let fails = exportFails;
  let rev = 0;
  const finisher = createFinisher({
    journal,
    canFinish,
    seal: async (sid, body) => {
      if (sealGate) await sealGate;
      calls.seals.push({ sid, ...body });
      return { revision: ++rev, replay_ok: true, review_logging: true };
    },
    exportAll: async () => {
      calls.exports++;
      if (fails > 0) { fails--; throw new Error('export failed'); }
    },
  });
  return { finisher, calls, journal };
}

const snap = (sessionId = 's1') => ({ sessionId, rows: [{ id: 'a' }], reviewedIds: ['a'] });

test('a click while a run is in progress does nothing (no second seal, no second export)', async () => {
  let release;
  const sealGate = new Promise((r) => { release = r; });
  const { finisher, calls } = harness({ sealGate });
  const first = finisher.run(snap());
  const second = await finisher.run(snap());
  assert.deepEqual(second, { status: 'busy' });
  release();
  assert.deepEqual(await first, { status: 'done', revision: 1 });
  assert.equal(calls.seals.length, 1);
  assert.equal(calls.exports, 1);
});

test('blocked while flagged rows remain: nothing flushed, sealed or exported', async () => {
  const { finisher, calls, journal } = harness({ canFinish: () => false });
  assert.deepEqual(await finisher.run(snap()), { status: 'blocked' });
  assert.equal(journal.flushes, 0);
  assert.equal(calls.seals.length, 0);
  assert.equal(calls.exports, 0);
});

test('refuses to seal while the server still lacks events, and frees the run', async () => {
  const journal = fakeJournal({ lastSeq: 5, ackTo: 4 });
  const { finisher, calls } = harness({ journal });
  await assert.rejects(finisher.run(snap()), /still saving/);
  assert.equal(calls.seals.length, 0);
  assert.equal(calls.exports, 0);
  journal.ackTo = null;   // the server caught up; the failed run must not leave it 'busy'
  assert.deepEqual(await finisher.run(snap()), { status: 'done', revision: 1 });
});

test('seal body carries the writer, final seq and the full snapshot', async () => {
  const { finisher, calls } = harness();
  await finisher.run(snap());
  assert.deepEqual(calls.seals[0], { sid: 's1', writer: 'w', final_seq: 3,
                                     rows: [{ id: 'a' }], reviewed_ids: ['a'] });
});

test('an export failure after a seal is retried without re-sealing', async () => {
  const { finisher, calls } = harness({ exportFails: 1 });
  await assert.rejects(finisher.run(snap()), /export failed/);
  assert.deepEqual(await finisher.run(snap()), { status: 'done', revision: 1 });
  assert.equal(calls.seals.length, 1);
  assert.equal(calls.exports, 2);
});

test('an edit after a finished review seals a new revision', async () => {
  const { finisher, calls, journal } = harness();
  await finisher.run(snap());
  journal.lastSeq = 4;
  assert.deepEqual(await finisher.run(snap()), { status: 'done', revision: 2 });
  assert.equal(calls.seals.length, 2);
  assert.equal(calls.seals[1].final_seq, 4);
});

test('logging off: no flush-gated seal, exports still run', async () => {
  const { finisher, calls } = harness({ journal: fakeJournal({ active: false }) });
  assert.deepEqual(await finisher.run(snap()), { status: 'done', revision: null });
  assert.equal(calls.seals.length, 0);
  assert.equal(calls.exports, 1);
});

test('a new session forgets the previous session\'s seal', async () => {
  const { finisher, calls } = harness();
  await finisher.run(snap('s1'));
  // Same lastSeq by coincidence, different session: it must still be sealed.
  assert.deepEqual(await finisher.run(snap('s2')), { status: 'done', revision: 2 });
  assert.equal(calls.seals.length, 2);
  assert.equal(calls.seals[1].sid, 's2');
});
