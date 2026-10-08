// createSerialRunner: the re-entrancy guard every router dispatch needs
// (native hashchange AND go()'s own dispatch can both try to start a route
// at once -- main.js's routeFromHash, Task 13). A run() that arrives while
// one is already in flight must not be DROPPED (a second Skip in quick
// succession must still land somewhere) and must not run CONCURRENTLY with
// the one in flight (two routeOnce()s racing the DOM would interleave
// screen swaps). Pure: runOnce is injected, so this needs no DOM.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { createSerialRunner } = await import('../../app/static/js/serial.js');

test('a single run() with nothing else in flight calls runOnce exactly once', async () => {
  let calls = 0;
  const { run } = createSerialRunner(async () => { calls++; });
  await run();
  assert.equal(calls, 1);
});

test('run() calls that arrive while busy never run runOnce concurrently', async () => {
  let active = 0;
  let overlapped = false;
  const releases = [];
  async function runOnce() {
    active++;
    if (active > 1) overlapped = true;
    await new Promise((resolve) => releases.push(resolve));
    active--;
  }
  const { run } = createSerialRunner(runOnce);
  const p1 = run();
  const p2 = run();   // arrives mid-flight: queued, not run yet
  releases[0]();
  await new Promise((resolve) => setTimeout(resolve, 0));   // let the queued re-run start
  assert.equal(releases.length, 2);   // the queued call DID start a second runOnce
  releases[1]();
  await p1;
  await p2;
  assert.equal(overlapped, false);
});

// The whole point of queueing rather than dropping: runOnce reads whatever
// is current itself (main.js's routeOnce reads location.hash), so the
// re-run after a busy period must see the LATEST state, not the one that
// happened to be current when the first extra run() arrived.
test('a run() that arrives mid-flight re-runs once more and sees the latest value', async () => {
  let current = 'a';
  const seen = [];
  let releaseFirst;
  const first = new Promise((resolve) => { releaseFirst = resolve; });
  let calls = 0;
  async function runOnce() {
    calls++;
    seen.push(current);
    if (calls === 1) await first;
  }
  const { run } = createSerialRunner(runOnce);
  const p1 = run();
  current = 'b';
  const p2 = run();   // queued while #1 is in flight
  current = 'c';
  const p3 = run();   // another arrival; still just queued once
  releaseFirst();
  await p1; await p2; await p3;
  assert.deepEqual(seen, ['a', 'c']);
  assert.equal(calls, 2);
});

test('an exception in runOnce still releases the guard for the next run()', async () => {
  let calls = 0;
  async function runOnce() {
    calls++;
    if (calls === 1) throw new Error('boom');
  }
  const { run } = createSerialRunner(runOnce);
  await assert.rejects(run(), /boom/);
  // The guard must be released even though the first run() rejected -- a
  // fresh run() (not one that was merely queued behind the failure) proves
  // `running` was reset in a `finally`, not left stuck true.
  await run();
  assert.equal(calls, 2);
});
