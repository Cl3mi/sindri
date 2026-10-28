// The review journal (docs/plans/2026-10-07-hitl-review-grading-design.md §2).
//
// Numbers each operation's event from 1, turns an undo into a `retract` of
// the event it undid, and flushes to the server, which is idempotent by seq
// and answers with the highest seq it holds contiguously. Everything above
// that stays pending and is resent, so a dropped request costs nothing.
// Pure apart from the injected `send`, so node can test it.

export function createJournal({ send, now = () => Date.now(),
                                wallNow = () => new Date().toISOString() }) {
  let sessionId = null;
  let writer = null;
  let seq = 0;
  let acked = 0;
  let t0 = 0;
  let pending = [];
  let inflight = null;
  let inflightTop = 0;   // highest seq included in the request `inflight` is for
  let gen = 0;           // bumped by every start(); a reply from a dead generation is ignored
  let seqOf = new WeakMap();   // op -> seq of its most recent 'do' event

  // Calls `send` IMMEDIATELY (not deferred to a microtask -- that would read
  // sessionId/writer late, see flush()), but still turns a SYNCHRONOUS throw
  // (a bad fetch call, not a rejected one) into a rejection the caller can
  // await, instead of throwing out of flush() itself.
  function callSend(sid, w, batch) {
    try {
      return Promise.resolve(send(sid, w, batch));
    } catch (err) {
      return Promise.reject(err);
    }
  }

  function start(sid, w) {
    sessionId = sid; writer = w || null;
    seq = 0; acked = 0; pending = []; inflight = null; inflightTop = 0;
    seqOf = new WeakMap();
    // A fresh session numbers from 1; a reopened one goes through resume().
    gen++;
    t0 = now();
  }

  // Continue a reopened session (batch design §3): number after the
  // server's last seq, treat everything up to it as acknowledged, and map
  // each restored op to its server seq so undoing it retracts the right
  // event. `lastSeq` is /open's `last_seq`, NOT the highest net event: net
  // events have holes where undone ops were retracted, and the store keeps
  // the first write of a seq, so reusing one would be dropped yet acked.
  function resume(sid, w, lastSeq, opSeqs = []) {
    // Same guard as the ack in flush(): a NaN/undefined/negative last_seq
    // would poison seq and acked, and every later event's numbering.
    if (!Number.isInteger(lastSeq) || lastSeq < 0) {
      throw new Error(`bad last_seq from server: ${lastSeq}`);
    }
    start(sid, w);
    seq = lastSeq;
    acked = lastSeq;
    for (const [op, s] of opSeqs) seqOf.set(op, s);
  }

  function record({ kind, op, event }) {
    // A log-only event (scan_read): something the reviewer did that is not an
    // operation on a row. Numbered and sent like any event, but never put in
    // seqOf, so no undo can ever retract it -- an undone scan is exactly the
    // attempt that missed (journal.py LOG_ONLY_TYPES).
    if (kind === 'log') {
      if (!writer || !event) return;
      pending.push({ ...event, seq: ++seq, t_wall: wallNow(), t_ms: now() - t0 });
      return;
    }
    if (!writer || typeof op.event !== 'function') return;
    if (kind !== 'do' && kind !== 'undo') return;   // an unknown kind is a bus bug, not an op
    if (kind === 'undo' && !seqOf.has(op)) return;  // done before logging started, or already retracted
    const base = { seq: ++seq, t_wall: wallNow(), t_ms: now() - t0 };
    if (kind === 'do') {
      // base spread AFTER the event's own fields, so nothing op.event()
      // returns can ever overwrite the seq/timestamps the journal assigns.
      pending.push({ ...op.event(), ...base });
      seqOf.set(op, base.seq);
    } else {
      pending.push({ ...base, type: 'retract', target: seqOf.get(op) });
      // A later undo of the same op with no intervening redo has nothing
      // left to retract; net_events() rejects a second retraction of the
      // same target, so forget the mapping instead of emitting one.
      seqOf.delete(op);
    }
  }

  function flush() {
    if (!writer) return Promise.resolve(acked);
    if (inflight) {
      // record() can add events above the batch already in flight; chain
      // behind that request rather than handing back its promise as-is --
      // its answer predates the new event, so it would otherwise never be
      // sent (the stranded-event bug). If the in-flight request itself
      // fails, this chained flush rejects without ever sending -- nothing
      // is lost, since the events are still in `pending` for the caller's
      // next retry/backoff to pick up.
      if (pending.length && pending[pending.length - 1].seq > inflightTop) {
        return inflight.then(() => flush());
      }
      return inflight;
    }
    if (!pending.length) return Promise.resolve(acked);
    const batch = pending.slice();
    // Captured here, synchronously, not read inside the `send(...)` call
    // below: that call is wrapped so a same-tick start() can run before the
    // request's own promise settles, and reading sessionId/writer at that
    // later point would stamp the request with whatever session start()
    // just switched to, not the one the batch actually belongs to.
    const sid = sessionId;
    const w = writer;
    const g = gen;
    inflightTop = batch[batch.length - 1].seq;
    inflight = callSend(sid, w, batch)
      .then(({ contiguous }) => {
        if (g !== gen) return acked;   // start() already replaced this session; ignore the stale reply
        if (!Number.isInteger(contiguous)) {
          throw new Error('bad ack from server');   // never let a NaN/undefined ack poison acked
        }
        acked = Math.max(acked, contiguous);
        pending = pending.filter((e) => e.seq > acked);
        return acked;
      })
      .finally(() => { if (g === gen) inflight = null; });
    return inflight;
  }

  return {
    start,
    resume,
    get active() { return writer !== null; },
    get sessionId() { return sessionId; },
    get writer() { return writer; },
    get lastSeq() { return seq; },
    get acked() { return acked; },
    get pending() { return pending.slice(); },
    record,
    flush,
  };
}
