// Reopening a drawing (batch design §3): the order of restore → resume →
// seed, what happens when the server's payload cannot be rebuilt, and the
// flush that must precede leaving a drawing. Pure, with injected fakes, so
// node can test what main.js only wires.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { restoreReview, flushBeforeLeaving, shouldReopen } = await import('../../app/static/js/reopen.js');
const { createJournal } = await import('../../app/static/js/journal.js');

function fakes({ restoreThrows = false } = {}) {
  const log = [];
  const session = { id: 'old' };
  const journal = createJournal({ send: async () => ({ contiguous: 0 }) });
  journal.start('old', 'w-old');
  return {
    log, session, journal,
    deps: {
      journal,
      restoreSession: (data, events) => {
        log.push(['restore', events.length]);
        if (restoreThrows) throw new Error('cannot restore a teleport event');
        session.id = data.session_id;
        return events.map((e) => [{ op: e.seq }, e.seq]);
      },
      clearSession: () => { log.push(['clear']); session.id = null; },
      beacon: (sid, w, events) => log.push(['beacon', sid, w, events.map((e) => e.seq)]),
      finisher: { seed: (s) => log.push(['seed', s]) },
    },
  };
}

const payload = (extra = {}) => ({ session_id: 'new', writer: 'w-new', last_seq: 4,
  events: [{ seq: 1 }, { seq: 4 }], final_revision: 2, sealed_seq: 4, ...extra });

test('restores, resumes after last_seq, and seeds the finisher with the sealed revision', () => {
  const f = fakes();
  restoreReview(payload(), f.deps);
  assert.equal(f.session.id, 'new');
  assert.equal(f.journal.sessionId, 'new');
  assert.equal(f.journal.writer, 'w-new');
  assert.equal(f.journal.lastSeq, 4);
  assert.deepEqual(f.log.at(-1), ['seed', { sessionId: 'new', seq: 4, revision: 2 }]);
});

test('a damaged last_seq is refused before anything is touched', () => {
  const f = fakes();
  assert.throws(() => restoreReview(payload({ last_seq: undefined }), f.deps), /review log/);
  assert.equal(f.session.id, 'old');
  assert.equal(f.journal.sessionId, 'old');
  assert.deepEqual(f.log, []);
});

test('an event that cannot be rebuilt leaves another drawing\'s session and journal intact', () => {
  const f = fakes({ restoreThrows: true });
  assert.throws(() => restoreReview(payload(), f.deps), /cannot restore/);
  assert.equal(f.session.id, 'old');
  assert.equal(f.journal.sessionId, 'old');
  assert.ok(f.journal.active);
});

test('...but on the SAME drawing /open already rotated the writer, so nothing stale survives', () => {
  const f = fakes({ restoreThrows: true });
  f.journal.start('new', 'w-stale');
  assert.throws(() => restoreReview(payload(), f.deps), /cannot restore/);
  assert.equal(f.session.id, null);
  assert.equal(f.journal.active, false);
});

test('if resuming fails after the restore, the half-built screen is cleared', () => {
  const f = fakes();
  f.deps.journal = { ...f.journal, resume: () => { throw new Error('boom'); },
                     start: (sid, w) => f.log.push(['start', sid, w]) };
  assert.throws(() => restoreReview(payload(), f.deps), /boom/);
  assert.equal(f.session.id, null);
  assert.deepEqual(f.log.slice(-2), [['clear'], ['start', null, null]]);
});

// ---- flushing before leaving a drawing ---------------------------------

function journalWith({ pending = 0, ackTo = null, fail = false } = {}) {
  return {
    active: true, lastSeq: 5, pending: Array(pending).fill({}),
    async flush() { if (fail) throw new Error('offline'); return ackTo ?? 5; },
  };
}

test('nothing pending: leaves without asking', async () => {
  let asked = 0;
  assert.equal(await flushBeforeLeaving(journalWith(), () => { asked++; return false; }), true);
  assert.equal(asked, 0);
});

test('an inactive journal has nothing to save', async () => {
  assert.equal(await flushBeforeLeaving({ active: false }, () => false), true);
});

test('pending events are flushed before leaving', async () => {
  let asked = 0;
  assert.equal(await flushBeforeLeaving(journalWith({ pending: 2 }), () => { asked++; return false; }), true);
  assert.equal(asked, 0);
});

test('a failed flush asks, and staying is honoured', async () => {
  assert.equal(await flushBeforeLeaving(journalWith({ pending: 2, fail: true }), () => false), false);
  assert.equal(await flushBeforeLeaving(journalWith({ pending: 2, fail: true }), () => true), true);
});

test('a flush the server only partly acknowledged also asks', async () => {
  let asked = 0;
  await flushBeforeLeaving(journalWith({ pending: 2, ackTo: 4 }), () => { asked++; return true; });
  assert.equal(asked, 1);
});

// ---- a drawing waiting behind an unsaved review -------------------------
// The reviewer chose to stay because the open drawing's changes had not
// saved; the drawing they were switching to must stay reachable and open by
// itself once the journal drains, never be forgotten.
const { afterFlush } = await import('../../app/static/js/reopen.js');

test('a drained journal opens the drawing that was waiting behind it', () => {
  assert.equal(afterFlush({ ok: true, pending: 0, pendingOpen: 'b' }), 'open');
});

test('events recorded during the flush keep it flushing before the switch', () => {
  assert.equal(afterFlush({ ok: true, pending: 2, pendingOpen: 'b' }), 'flush');
});

test('with nothing waiting, a successful flush just idles until the next edit', () => {
  assert.equal(afterFlush({ ok: true, pending: 2, pendingOpen: null }), 'idle');
  assert.equal(afterFlush({ ok: true, pending: 0, pendingOpen: null }), 'idle');
});

test('a failed flush backs off and retries, waiting drawing or not', () => {
  assert.equal(afterFlush({ ok: false, pending: 2, pendingOpen: 'b' }), 'retry');
  assert.equal(afterFlush({ ok: false, pending: 2, pendingOpen: null }), 'retry');
});

test('a writer taken by another tab stops retrying -- nothing more can save here', () => {
  assert.equal(afterFlush({ ok: false, stale: true, pending: 2, pendingOpen: null }), 'stale');
});

test('...and with a drawing waiting, opens it: the stale events can never save from this tab', () => {
  // Waiting for a drain that can never come would strand the waiting
  // drawing and block every upload with nothing on screen to undo it.
  assert.equal(afterFlush({ ok: false, stale: true, pending: 2, pendingOpen: 'b' }), 'open_stale');
});

test('never switches drawings in the middle of a Finish', () => {
  assert.equal(afterFlush({ ok: true, pending: 0, pendingOpen: 'b', finishing: true }), 'idle');
  assert.equal(afterFlush({ ok: false, stale: true, pending: 2, pendingOpen: 'b', finishing: true }), 'idle');
});

// ---- edits on A while /open(B) was in flight ------------------------------

test('events the old drawing still holds go out with ITS writer before resuming on the new one', () => {
  const f = fakes();
  f.journal.record({ kind: 'do', op: { event: () => ({ type: 'accept', ids: [] }) } });
  restoreReview(payload(), f.deps);
  const beacon = f.log.find((e) => e[0] === 'beacon');
  assert.deepEqual(beacon, ['beacon', 'old', 'w-old', [1]]);
  assert.ok(f.log.indexOf(beacon) < f.log.findIndex((e) => e[0] === 'seed'));
  assert.equal(f.journal.sessionId, 'new');
});

test('nothing pending, or the same drawing, sends no beacon', () => {
  const f = fakes();
  restoreReview(payload(), f.deps);
  assert.equal(f.log.filter((e) => e[0] === 'beacon').length, 0);
  const g = fakes();
  g.journal.start('new', 'w-stale');
  g.journal.record({ kind: 'do', op: { event: () => ({ type: 'accept', ids: [] }) } });
  restoreReview(payload(), g.deps);   // /open rotated that writer: a beacon would only be refused
  assert.equal(g.log.filter((e) => e[0] === 'beacon').length, 0);
});

// ---- shouldReopen: the defense-in-depth half of the re-open bug ----------
// Re-opening the drawing already open would call /open again, which mints a
// NEW writer and journal.resume() wipes `pending` -- so unflushed edits are
// silently lost the moment a reviewer re-selects their own open drawing
// (Jump listing it, or a hash re-dispatch to the same #/doc/<id>). The
// primary fix excludes the open drawing from the Jump palette's candidates
// (reviewbar.js); this is the second line of defense for every other path
// that can still ask to open `id` -- a stale link, a race, a bookmark.
test('shouldReopen is false for the drawing already open', () => {
  const journal = createJournal({ send: async () => ({ contiguous: 0 }) });
  journal.start('open-id', 'w1');
  assert.equal(shouldReopen(journal, 'open-id'), false);
});

test('shouldReopen is true for any other drawing, or when nothing is open yet', () => {
  const journal = createJournal({ send: async () => ({ contiguous: 0 }) });
  assert.equal(shouldReopen(journal, 'anything'), true);   // journal.active is false
  journal.start('open-id', 'w1');
  assert.equal(shouldReopen(journal, 'other-id'), true);
});
