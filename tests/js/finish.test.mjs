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
      return { revision: ++rev, replay_ok: true };
    },
    exportAll: async (s, revision) => {
      calls.exports++;
      calls.exportedRevision = revision;
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

test('a drawing without a review log cannot be finished', async () => {
  const { finisher, calls } = harness({ journal: fakeJournal({ active: false }) });
  await assert.rejects(finisher.run(snap()), /reopen/);
  assert.equal(calls.seals.length, 0);
  assert.equal(calls.exports, 0);
});

test('export receives the revision it must render', async () => {
  const { finisher, calls } = harness();
  await finisher.run(snap());
  assert.equal(calls.exportedRevision, 1);
});

test('a new session forgets the previous session\'s seal', async () => {
  const { finisher, calls } = harness();
  await finisher.run(snap('s1'));
  // Same lastSeq by coincidence, different session: it must still be sealed.
  assert.deepEqual(await finisher.run(snap('s2')), { status: 'done', revision: 2 });
  assert.equal(calls.seals.length, 2);
  assert.equal(calls.seals[1].sid, 's2');
});

// ---- seeded from the server after a reload ------------------------------
// The `sealed` memory above lives in one tab. After a reload /open says what
// the server last sealed (final_revision at sealed_seq); store.seal would
// accept that same seq again, so without the seed a Finish with no new edits
// would mint a redundant revision.

test('a reopened, already-sealed review re-exports its revision without sealing', async () => {
  const { finisher, calls } = harness({ journal: fakeJournal({ lastSeq: 3 }) });
  finisher.seed({ sessionId: 's1', seq: 3, revision: 2 });
  assert.deepEqual(await finisher.run(snap()), { status: 'done', revision: 2 });
  assert.equal(calls.seals.length, 0);
  assert.equal(calls.exportedRevision, 2);
});

test('an edit after the reopen still seals a new revision', async () => {
  const { finisher, calls } = harness({ journal: fakeJournal({ lastSeq: 4 }) });
  finisher.seed({ sessionId: 's1', seq: 3, revision: 2 });
  await finisher.run(snap());
  assert.equal(calls.seals.length, 1);
  assert.equal(calls.seals[0].final_seq, 4);
});

test('a seed for another session, or with nothing sealed, is ignored', async () => {
  const { finisher, calls } = harness({ journal: fakeJournal({ lastSeq: 3 }) });
  finisher.seed({ sessionId: 's2', seq: 3, revision: 2 });
  await finisher.run(snap('s1'));
  assert.equal(calls.seals.length, 1);
  const h2 = harness({ journal: fakeJournal({ lastSeq: 0 }) });
  h2.finisher.seed({ sessionId: 's1', seq: 0, revision: null });
  await h2.finisher.run(snap('s1'));
  assert.equal(h2.calls.seals.length, 1);   // never sealed: revision null is no seal
});

// ---- per-kind export memory ---------------------------------------------

const { createExporter } = await import('../../app/static/js/finish.js');

function exporterHarness(failOnce = new Set()) {
  const got = [];
  const exportAll = createExporter(async (sid, kind, rev) => {
    if (failOnce.has(kind)) { failOnce.delete(kind); throw new Error(`${kind} failed`); }
    got.push(`${sid}:${kind}:r${rev}`);
  });
  return { exportAll, got };
}

test('a retried partial export does not download the file that already arrived', async () => {
  const { exportAll, got } = exporterHarness(new Set(['pdf']));
  await assert.rejects(exportAll({ sessionId: 's1' }, 1), /pdf failed/);
  await exportAll({ sessionId: 's1' }, 1);
  assert.deepEqual(got, ['s1:xlsx:r1', 's1:pdf:r1']);
});

test('a complete export forgets, so asking again downloads both again', async () => {
  const { exportAll, got } = exporterHarness();
  await exportAll({ sessionId: 's1' }, 1);
  await exportAll({ sessionId: 's1' }, 1);
  assert.deepEqual(got, ['s1:xlsx:r1', 's1:pdf:r1', 's1:xlsx:r1', 's1:pdf:r1']);
});

test('a partial export of one revision does not excuse the next revision', async () => {
  const { exportAll, got } = exporterHarness(new Set(['pdf']));
  await assert.rejects(exportAll({ sessionId: 's1' }, 1));
  await exportAll({ sessionId: 's1' }, 2);
  assert.deepEqual(got, ['s1:xlsx:r1', 's1:xlsx:r2', 's1:pdf:r2']);
});
