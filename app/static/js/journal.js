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
  let seqOf = new WeakMap();   // op -> seq of its most recent 'do' event

  return {
    start(sid, w) {
      sessionId = sid; writer = w || null;
      seq = 0; acked = 0; pending = []; inflight = null; seqOf = new WeakMap();
      t0 = now();
    },
    get active() { return writer !== null; },
    get sessionId() { return sessionId; },
    get writer() { return writer; },
    get lastSeq() { return seq; },
    get acked() { return acked; },
    get pending() { return pending.slice(); },

    record({ kind, op }) {
      if (!writer || typeof op.event !== 'function') return;
      if (kind === 'undo' && !seqOf.has(op)) return;   // done before logging started
      const base = { seq: ++seq, t_wall: wallNow(), t_ms: now() - t0 };
      if (kind === 'do') {
        pending.push({ ...base, ...op.event() });
        seqOf.set(op, base.seq);
      } else {
        pending.push({ ...base, type: 'retract', target: seqOf.get(op) });
      }
    },

    flush() {
      if (!writer) return Promise.resolve(acked);
      if (inflight) return inflight;
      if (!pending.length) return Promise.resolve(acked);
      const batch = pending.slice();
      inflight = send(sessionId, writer, batch)
        .then(({ contiguous }) => {
          acked = Math.max(acked, contiguous);
          pending = pending.filter((e) => e.seq > acked);
          return acked;
        })
        .finally(() => { inflight = null; });
      return inflight;
    },
  };
}
