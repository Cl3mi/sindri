// The review journal (docs/plans/2026-10-07-hitl-review-grading-design.md §2).
//
// Numbers each operation's event from 1, turns an undo into a `retract` of
// the event it undid, and flushes to the server, which is idempotent by seq
// and answers with the highest seq it holds contiguously. Everything above
// that stays pending and is resent, so a dropped request costs nothing.
// Pure apart from the injected `send`, so node can test it.

export function createJournal({ send, now = () => Date.now(),
                                wallNow = () => new Date().toISOString(),
                                onLoggingOff = () => {} }) {
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
    // Numbering restarts at 1 because a writer token is issued only for a
    // fresh session; a future reopen path must resume from the server's
    // contiguous seq instead of zero.
    gen++;
    t0 = now();
  }

  function record({ kind, op }) {
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
      .then(({ contiguous, review_logging }) => {
        if (g !== gen) return acked;   // start() already replaced this session; ignore the stale reply
        if (review_logging === false) {
          // The server's own answer when its store is gone mid-session (a
          // restart without SINDRI_REVIEW_DIR, or a dir that went
          // unwritable): {contiguous: null, review_logging: false}. That is
          // not malformed, it is the server telling us to stop -- switch
          // logging off for the rest of this session instead of rejecting,
          // or every later flush (and so Finish) would keep failing for a
          // session that was never going to be recorded again anyway.
          writer = null;
          pending = [];
          onLoggingOff();
          return acked;
        }
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
