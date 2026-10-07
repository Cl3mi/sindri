// Reopening a drawing (batch design §3), kept out of main.js so node can
// test the ordering main.js only wires. Pure apart from the injected deps.

// Rebuild a review from POST /open's payload: restore the session with the
// server's net events, continue the journal after `last_seq`, and tell the
// finisher what the server last sealed. Throws -- with the screen either
// untouched or cleared, never half-built -- when the payload cannot be
// rebuilt; the caller shows the error.
export function restoreReview(data, { restoreSession, clearSession, journal, finisher }) {
  // Checked before anything is touched. journal.resume() would refuse it
  // too, but only AFTER restoreSession had already swapped the screen.
  if (!Number.isInteger(data.last_seq) || data.last_seq < 0) {
    throw new Error('The server sent a damaged review log position.');
  }
  let pairs;
  try {
    // Builds every op before touching the session (state.js), so a throw
    // here leaves the previous screen as it was.
    pairs = restoreSession(data, data.events || []);
  } catch (err) {
    // /open has already rotated this drawing's writer: a journal still on
    // it would only ever be refused. Another drawing's journal is still
    // valid and stays, with its screen.
    if (journal.sessionId === data.session_id) {
      clearSession();
      journal.start(null, null);
    }
    throw err;
  }
  try {
    journal.resume(data.session_id, data.writer, data.last_seq, pairs);
  } catch (err) {
    // The screen now shows the new drawing but the journal does not: any
    // edit would go unrecorded. Clear both rather than leave that.
    clearSession();
    journal.start(null, null);
    throw err;
  }
  finisher.seed({ sessionId: data.session_id, seq: data.sealed_seq,
                  revision: data.final_revision });
  return pairs;
}

// Before leaving the drawing the journal is on (opening another, or
// reopening this one): journal.start()/resume() discard unflushed events
// and /open rotates the writer, so flush first. Resolves true to go ahead.
// When the server does not take everything, `confirmLeave()` decides --
// the caller asks the reviewer; nothing is dropped silently.
export async function flushBeforeLeaving(journal, confirmLeave) {
  if (!journal.active || !journal.pending.length) return true;
  let acked = -1;
  try { acked = await journal.flush(); } catch { /* offline, refused: ask */ }
  if (acked >= journal.lastSeq) return true;
  return !!confirmLeave();
}
