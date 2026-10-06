// The review journal (docs/plans/2026-10-07-hitl-review-grading-design.md §2):
// each state.js operation emits one self-contained event; an undo emits a
// retraction of the event it undid.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const S = await import('../../app/static/js/state.js');

function row(id, extra = {}) {
  return { id, pos: 0, char_type: 'Distance', nominal: '1', upper_tol: '',
           lower_tol: '', needs_review: true, review_reasons: [],
           target_region: [0, 0, 10, 10], balloon_xy: [1, 1], suggested: false, ...extra };
}
function load(rows) { S.setSession({ session_id: 's', image_url: '', rows, notes: null }); }
function capture() {
  const seen = [];
  const off = S.on('op', (e) => seen.push({ kind: e.kind, event: e.op.event() }));
  return { seen, off };
}

test('each operation describes itself after it ran', () => {
  load([row('a'), row('b'), row('s', { suggested: true })]);
  const { seen, off } = capture();
  S.apply(S.opEditCell('a', 'nominal', '2'));
  S.apply(S.opMoveRow('a', [3, 4]));
  S.apply(S.opDeleteRow('b'));
  S.apply(S.opAddRow(row('m', { source: 'manual' })));
  S.apply(S.opConfirmSuggestions(['s']));
  S.apply(S.opBulkReview(['a'], true));
  S.apply(S.opBulkReview(['a'], false));
  off();
  assert.deepEqual(seen.map((x) => x.event.type),
    ['edit_cell', 'move_row', 'delete_row', 'add_row', 'confirm_suggestions', 'accept', 'unaccept']);
  assert.deepEqual(seen[0].event, { type: 'edit_cell', id: 'a', field: 'nominal', old: '1', new: '2' });
  assert.deepEqual(seen[1].event, { type: 'move_row', id: 'a', xy: [3, 4] });
  assert.equal(seen[3].event.row.id, 'm');
});

test('undo and redo travel the bus as undo and do', () => {
  load([row('a')]);
  const { seen, off } = capture();
  S.apply(S.opEditCell('a', 'nominal', '2'));
  S.undo();
  S.redo();
  off();
  assert.deepEqual(seen.map((x) => x.kind), ['do', 'undo', 'do']);
});

const { createJournal } = await import('../../app/static/js/journal.js');

function fakeOp(ev) { return { event: () => ev }; }
function fakeServer() {
  const held = new Set();
  const calls = [];
  return {
    calls,
    fail: false,
    async send(sid, writer, events) {
      calls.push(events.map((e) => e.seq));
      if (this.fail) throw new Error('offline');
      events.forEach((e) => held.add(e.seq));
      let n = 0; while (held.has(n + 1)) n++;
      return { contiguous: n };
    },
  };
}

test('numbers events from 1 and turns an undo into a retraction', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0 });
  j.start('s', 'w');
  const op = fakeOp({ type: 'accept', ids: ['a'] });
  j.record({ kind: 'do', op });
  j.record({ kind: 'undo', op });
  assert.deepEqual(j.pending.map((e) => [e.seq, e.type, e.target]),
    [[1, 'accept', undefined], [2, 'retract', 1]]);
});

test('a redo is a fresh event, and undoing it retracts the fresh seq', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0 });
  j.start('s', 'w');
  const op = fakeOp({ type: 'delete_row', id: 'b' });
  j.record({ kind: 'do', op });     // 1
  j.record({ kind: 'undo', op });   // 2 retract 1
  j.record({ kind: 'do', op });     // 3
  j.record({ kind: 'undo', op });   // 4 retract 3
  assert.equal(j.pending.at(-1).target, 3);
});

test('records nothing without a writer (logging off)', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }) });
  j.start('s', null);
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: [] }) });
  assert.equal(j.pending.length, 0);
  assert.equal(j.active, false);
});

test('flush drops what the server acknowledged and keeps the rest', async () => {
  const srv = fakeServer();
  const j = createJournal({ send: srv.send.bind(srv), now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['b'] }) });
  assert.equal(await j.flush(), 2);
  assert.equal(j.pending.length, 0);
  assert.equal(j.acked, 2);
  assert.equal(j.lastSeq, 2);
});

test('a failed flush keeps every event for the next attempt', async () => {
  const srv = fakeServer(); srv.fail = true;
  const j = createJournal({ send: srv.send.bind(srv), now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  await assert.rejects(j.flush());
  assert.equal(j.pending.length, 1);
  srv.fail = false;
  assert.equal(await j.flush(), 1);
});

test('concurrent flushes share one request', async () => {
  const srv = fakeServer();
  const j = createJournal({ send: srv.send.bind(srv), now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  await Promise.all([j.flush(), j.flush()]);
  assert.equal(srv.calls.length, 1);
});
