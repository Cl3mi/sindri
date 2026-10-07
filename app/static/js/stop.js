// Stop & remove (batch design §2 queue controls). Cancel is cooperative:
// the worker stops at its next progress report, and DELETE answers 409
// until it has -- legitimately many minutes, since detection reports no
// progress. A 409 is therefore waited out without limit. Any other failure
// (a 500, a dropped network) is not "still stopping", and after a few in a
// row the UI says so and offers a way out instead of spinning forever; the
// drawing stays cancelled on the server either way.

export const STOP_GIVE_UP_AFTER = 5;

// `res` is api.deleteDocument's { ok, status }; `failures` the consecutive
// non-409 failures so far. Returns the next step and the new count.
export function nextStopStep(res, failures) {
  // 404: already gone (removed elsewhere, or an earlier DELETE landed
  // though its reply was lost) -- exactly what Stop wanted.
  if (res.ok || res.status === 404) return { step: 'done', failures: 0 };
  if (res.status === 409) return { step: 'wait', failures: 0 };
  const n = failures + 1;
  return { step: n >= STOP_GIVE_UP_AFTER ? 'give_up' : 'retry', failures: n };
}
