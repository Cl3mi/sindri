// Stop removes the drawing once the worker lets go of it (batch design §2
// queue controls). DELETE answers 409 for as long as a cancelled run is
// unwinding -- legitimately many minutes, since detection reports no
// progress -- so 409 is waited out indefinitely. Any OTHER failure (500,
// network) is not "still stopping": after a few in a row the UI must say so
// and offer a way out instead of spinning forever.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { nextStopStep, STOP_GIVE_UP_AFTER } = await import('../../app/static/js/stop.js');

test('a removed drawing is done', () => {
  assert.deepEqual(nextStopStep({ ok: true, status: 200 }, 3), { step: 'done', failures: 0 });
});

test('409 means the worker is still stopping: wait, however long, and do not count it', () => {
  assert.deepEqual(nextStopStep({ ok: false, status: 409 }, 2), { step: 'wait', failures: 0 });
});

test('other failures retry, counting consecutive ones', () => {
  assert.deepEqual(nextStopStep({ ok: false, status: 500 }, 0), { step: 'retry', failures: 1 });
  assert.deepEqual(nextStopStep({ ok: false, status: 0 }, 1), { step: 'retry', failures: 2 });
});

test('enough consecutive non-409 failures give up visibly', () => {
  assert.deepEqual(nextStopStep({ ok: false, status: 500 }, STOP_GIVE_UP_AFTER - 1),
                   { step: 'give_up', failures: STOP_GIVE_UP_AFTER });
});
