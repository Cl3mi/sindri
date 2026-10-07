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

// --- Opus reviewer's fuzzed-contract follow-up (probe/race.mjs, strand.mjs) ---

test("a stale reply after start() cannot ack or drop another session's events", async () => {
  // race.mjs case 1: A's slow request answers only after start('B') has
  // already begun a new session. Without a generation counter, A's late
  // `finally` clears B's inflight handle (breaking single-flight) and A's
  // contiguous count floods into B's `acked`, silently dropping B's own
  // unacknowledged events.
  let resolveA;
  const calls = [];
  const send = (sid, w, evs) => {
    calls.push([sid, evs.map((e) => e.seq)]);
    if (sid === 'A') return new Promise((r) => { resolveA = r; });
    return new Promise(() => {});   // B's own request never answers in this test
  };
  const j = createJournal({ send, now: () => 0 });
  j.start('A', 'wa');
  for (let i = 0; i < 3; i++) j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['x'] }) });
  const pa = j.flush();
  j.start('B', 'wb');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['y'] }) });
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['z'] }) });
  const pb = j.flush();            // B's own request, now in flight
  resolveA({ contiguous: 3 });     // A's late answer must not touch B
  await pa;
  assert.equal(j.sessionId, 'B');
  assert.equal(j.acked, 0);
  assert.deepEqual(j.pending.map((e) => e.seq), [1, 2]);
  assert.equal(calls.length, 2);   // A's request and B's own -- no overlapping B sends
  void pb;                         // B's request is left unanswered on purpose
});

test('flush() captures the session at call time, not when the request later fires', async () => {
  // race2.mjs case A: flush() then start() in the SAME tick. The original
  // fix read sessionId/writer inside the deferred `.then(() => send(...))`
  // callback, so a same-tick start() had already overwritten them by the
  // time send() actually ran -- A's batch went out stamped 'B'/'wb'.
  const calls = [];
  const send = async (sid, w, evs) => {
    calls.push([sid, w, evs.map((e) => e.type)]);
    return { contiguous: evs.length };
  };
  const j = createJournal({ send, now: () => 0 });
  j.start('A', 'wa');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['A-row'] }) });
  const p = j.flush();
  j.start('B', 'wb');   // same tick -- must not change what the in-flight request is sent as
  await p;
  assert.deepEqual(calls, [['A', 'wa', ['accept']]]);
  assert.equal(j.sessionId, 'B');
  assert.equal(j.pending.length, 0);
});

test('flush() after an in-flight request does not strand a newly recorded event', async () => {
  // strand.mjs: flush() means "everything recorded so far". A flush() called
  // while a request for an earlier batch is still in flight must not just
  // hand back that stale promise once a newer event exists -- it has to
  // chain its own request behind it, or the newer event is never sent.
  const held = new Set();
  const sends = [];
  const resolvers = [];
  const send = (sid, w, evs) => {
    sends.push(evs.map((e) => e.seq));
    return new Promise((resolve) => {
      resolvers.push(() => {
        evs.forEach((e) => held.add(e.seq));
        let n = 0; while (held.has(n + 1)) n++;
        resolve({ contiguous: n });
      });
    });
  };
  const j = createJournal({ send, now: () => 0 });
  j.start('A', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  const p1 = j.flush();                 // batch [1] goes out, slow
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['b'] }) });
  const p2 = j.flush();                 // seq 2 exists meanwhile -- must not be dropped
  resolvers[0]();                       // server answers the first request
  await p1;
  while (resolvers.length < 2) await new Promise((r) => setImmediate(r));
  resolvers[1]();                       // server answers the chained second request
  assert.equal(await p2, 2);
  assert.equal(j.pending.length, 0);
  assert.deepEqual(sends, [[1], [2]]);
});

test('a malformed ack (non-integer contiguous) rejects instead of corrupting pending', async () => {
  const j = createJournal({ send: async () => ({}), now: () => 0 });   // no `contiguous` key
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  await assert.rejects(j.flush(), /bad ack/);
  assert.equal(j.pending.length, 1);
  assert.equal(j.acked, 0);
});

test('a null ack with review_logging:false switches logging off instead of throwing', async () => {
  // The server's answer when its own store disappeared mid-session (restart
  // without SINDRI_REVIEW_DIR, or a dir that went unwritable): {contiguous:
  // null, review_logging: false}. Before this, that null failed the same
  // Number.isInteger check as a truly malformed ack and rejected forever,
  // so every later flush (and therefore Finish) kept failing for a session
  // the server was never going to log again.
  let offCalls = 0;
  const j = createJournal({
    send: async () => ({ contiguous: null, review_logging: false }),
    now: () => 0,
    onLoggingOff: () => { offCalls++; },
  });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  assert.equal(await j.flush(), 0);
  assert.equal(j.active, false);
  assert.equal(j.pending.length, 0);
  assert.equal(offCalls, 1);
  // Later record() calls are then a no-op, same as logging never having started.
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['b'] }) });
  assert.equal(j.pending.length, 0);
});

test('onLoggingOff defaults to a no-op when the caller does not pass one', async () => {
  const j = createJournal({ send: async () => ({ contiguous: null, review_logging: false }), now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  await assert.doesNotReject(j.flush());
  assert.equal(j.active, false);
});

test('a plain malformed ack (no review_logging:false) still rejects', async () => {
  const j = createJournal({ send: async () => ({ contiguous: null }), now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  await assert.rejects(j.flush(), /bad ack/);
  assert.equal(j.active, true);
});

test('a synchronous throw from send is wrapped into a rejection, not thrown from flush()', async () => {
  const j = createJournal({ send: () => { throw new Error('sync'); }, now: () => 0 });
  j.start('s', 'w');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  let p;
  assert.doesNotThrow(() => { p = j.flush(); });
  await assert.rejects(p, /sync/);
  assert.equal(j.pending.length, 1);
});

test('record ignores a kind it does not know (e.g. a stale "redo" label)', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0 });
  j.start('s', 'w');
  const op = fakeOp({ type: 'accept', ids: ['x'] });
  j.record({ kind: 'do', op });
  j.record({ kind: 'redo', op });
  assert.deepEqual(j.pending.map((e) => [e.seq, e.type, e.target]), [[1, 'accept', undefined]]);
});

test('a second undo of the same op (no intervening redo) is skipped, not a duplicate retraction', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0 });
  j.start('s', 'w');
  const op = fakeOp({ type: 'accept', ids: ['x'] });
  j.record({ kind: 'do', op });
  j.record({ kind: 'undo', op });
  j.record({ kind: 'undo', op });   // stale: net_events() would reject a second retraction
  assert.equal(j.pending.length, 2);
});

test('undo of an op done before start() is skipped; its later redo is a fresh do', () => {
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0 });
  const op = fakeOp({ type: 'accept', ids: ['x'] });
  // op was done before start() -- the journal was not recording yet.
  j.start('s', 'w');
  j.record({ kind: 'undo', op });
  assert.equal(j.pending.length, 0);
  assert.equal(j.lastSeq, 0);
  j.record({ kind: 'do', op });     // redo of that same op, now logged, is a fresh event
  assert.deepEqual(j.pending.map((e) => [e.seq, e.type]), [[1, 'accept']]);
});

test('start() resets seq, acked and pending for the new session', async () => {
  const srv = fakeServer();
  const j = createJournal({ send: srv.send.bind(srv), now: () => 0 });
  j.start('s1', 'w1');
  j.record({ kind: 'do', op: fakeOp({ type: 'accept', ids: ['a'] }) });
  await j.flush();
  assert.equal(j.lastSeq, 1);
  assert.equal(j.acked, 1);
  j.start('s2', 'w2');
  assert.equal(j.lastSeq, 0);
  assert.equal(j.acked, 0);
  assert.equal(j.pending.length, 0);
});
