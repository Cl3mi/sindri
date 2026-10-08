// A serial-execution guard for a function that reads its own state at call
// time rather than taking it as an argument (main.js's routeFromHash: each
// call reads `location.hash`, not a hash passed in). Two things can both
// try to start a route dispatch at once -- a native `hashchange` (a real
// link click, back/forward) and `go()`'s own dispatch -- and a route that
// arrives while one is already in flight must be neither DROPPED (two
// Skips in quick succession must still land on the SECOND target) nor run
// CONCURRENTLY with the one in flight (two dispatches racing the DOM would
// interleave screen swaps). Pure: `runOnce` is injected, so this needs no
// DOM and is reusable by anything with the same shape of problem.
export function createSerialRunner(runOnce) {
  let running = false;
  let again = false;

  async function run() {
    // A call that arrives while one is already in flight only raises the
    // flag and returns -- it does not wait for its own turn. The in-flight
    // run's own loop (below) is what re-invokes `runOnce`, and by the time
    // it does, `runOnce` reads whatever is current (the latest hash), not a
    // snapshot taken when this call arrived -- so a THIRD call arriving
    // before the re-run starts costs nothing extra: `again` is already true.
    if (running) { again = true; return; }
    running = true;
    try {
      // `again` is cleared before each call, not after: a call that arrives
      // WHILE `runOnce` is executing must still cause one more pass, so the
      // flag has to be live for runOnce's whole duration, not just the gap
      // between iterations.
      do {
        again = false;
        await runOnce();
      } while (again);
    } finally {
      // Always releases the guard, including when runOnce throws -- an
      // exception must not wedge every future call() behind a `running`
      // flag nothing will ever clear. The exception itself still propagates
      // to this run()'s own caller.
      running = false;
    }
  }

  return { run };
}
