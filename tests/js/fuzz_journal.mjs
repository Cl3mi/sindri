// Fuzzes state.js + journal.js through random reviewer action sequences and
// prints, as JSON on stdout, one record per session: the proposal rows, the
// journal's net-pending events, the final row state, and which ids ended up
// reviewed. tests/review/test_journal_replay_parity.py replays each session
// in Python (app/review/replay.py) and asserts it reproduces the final rows
// exactly -- the cross-language contract the whole journal depends on.
//
// NOT named *.test.mjs on purpose: tests/test_js_state.py's `node --test`
// glob must not pick this up as a test file in its own right; it is a data
// generator invoked as a subprocess by the Python test.
//
// A fixed seed (first CLI arg) makes this deterministic, so a real
// regression always reproduces instead of flaking in and out.
//
// Pre-start ops are deliberately NOT generated: the real UI only starts
// emitting to the journal once a writer token exists (main.js wires
// journal.start() before any op can fire), so a pre-start do/undo pair is
// unreachable and would only test a scenario the product never hits.
const S = await import('../../app/static/js/state.js');
const { createJournal } = await import('../../app/static/js/journal.js');

let seed = Number(process.argv[2] || 12345);
const rnd = () => { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648; };
const pick = (a) => a[Math.floor(rnd() * a.length)];

// Mirror of app/review/journal.py net_events, for the simulated reload.
function netEvents(events) {
  const live = new Map();
  for (const e of [...events].sort((a, b) => a.seq - b.seq)) {
    if (e.type === 'retract') live.delete(e.target);
    else live.set(e.seq, e);
  }
  return [...live.values()];
}
// Third CLI arg 'resume': each session is reloaded mid-way from what the
// server would hold (proposal + net events + last_seq, as /open returns
// them) and then keeps going, undos into restored operations included.
const RESUME = process.argv[4] === 'resume';

function row(id, extra = {}) {
  return { id, pos: 0, char_type: 'Distance', nominal: String(Math.floor(rnd() * 9)), upper_tol: '',
           lower_tol: '', needs_review: rnd() < 0.5, review_reasons: [],
           target_region: [rnd() * 500, rnd() * 500, 600, 600],
           balloon_xy: rnd() < 0.2 ? null : [rnd() * 100, rnd() * 100],
           suggested: rnd() < 0.3, ...extra };
}

const N = Number(process.argv[3] || 300);
const cases = [];
let addN = 0;

function randomSteps(steps, ids, log) {
  for (let k = 0; k < steps; k++) {
    const anyIds = [...new Set([...ids, ...S.state.rows.map((r) => r.id), 'zz'])];
    const r = rnd();
    let label;
    if (r < 0.18) { S.undo(); label = 'undo'; }
    else if (r < 0.30) { S.redo(); label = 'redo'; }
    else if (r < 0.45) {
      const id = pick(anyIds); const f = pick(['char_type', 'nominal', 'upper_tol', 'lower_tol']);
      const v = pick(['', '1', '2', 'Diameter', '+0.1']);
      const reason = pick([undefined, undefined, 'misread_digit', 'other']);
      S.apply(S.opEditCell(id, f, v, reason)); label = `edit ${id}.${f}=${v}`;
    } else if (r < 0.55) {
      const id = pick(anyIds); S.apply(S.opMoveRow(id, [Math.round(rnd() * 100), 7])); label = `move ${id}`;
    } else if (r < 0.62) {
      const id = pick(anyIds);
      S.apply(S.opDeleteRow(id, pick([undefined, 'duplicate', 'wrong_region'])));
      label = `del ${id}`;
    } else if (r < 0.67) {
      const id = 'm' + (addN++);
      S.apply(S.opAddRow({ ...row(id), suggested: false, reviewed: false, source: 'manual' }));
      label = `add ${id}`;
    } else if (r < 0.72) {
      const id = pick(anyIds); const x = Math.round(rnd() * 400);
      S.apply(S.opMoveTarget(id, [x, 5, x + 20, 30])); label = `anchor ${id}`;
    } else if (r < 0.76) {
      const t = pick(['', 'policy', 'we never balloon chamfers']);
      S.apply(S.opSetNote(t)); label = `note ${t}`;
    } else if (r < 0.82) {
      const sel = anyIds.filter(() => rnd() < 0.4); S.apply(S.opConfirmSuggestions(sel)); label = `confirm ${sel}`;
    } else if (r < 0.90) {
      const sel = anyIds.filter(() => rnd() < 0.4); S.apply(S.opFlagRows(sel)); label = `flag ${sel}`;
    } else {
      const sel = anyIds.filter(() => rnd() < 0.4); const t = rnd() < 0.6;
      S.apply(S.opBulkReview(sel, t)); label = `${t ? 'accept' : 'unaccept'} ${sel}`;
    }
    log.push(label);
  }
}

for (let c = 0; c < N; c++) {
  const ids = ['a', 'b', 'c', 'd', 'e'];
  const proposal = ids.map((i) => row(i));
  S.setSession({ session_id: 's' + c, image_url: '', rows: JSON.parse(JSON.stringify(proposal)), notes: null });
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0, wallNow: () => '' });
  j.start('s' + c, 'w');
  const off = S.on('op', (e) => j.record(e));
  const steps = 2 + Math.floor(rnd() * 14);
  const log = [];
  randomSteps(steps, ids, log);
  off();
  let before = j.pending;
  let events = before;
  let lastSeq = j.lastSeq;
  let restoredRows = null;
  let restoredReviewed = null;
  let restoredNote = null;
  if (RESUME) {
    // Simulated reload: rebuild from what the server holds, then keep going
    // -- including undos that reach back into restored operations. lastSeq
    // is the journal's own top seq, which sits ABOVE every net event
    // whenever the session ended on an undo (a post-seal reopen's shape).
    const pairs = S.restoreSession(
      { session_id: 's' + c, image_url: '', rows: JSON.parse(JSON.stringify(proposal)), notes: null },
      netEvents(before));
    restoredRows = JSON.parse(JSON.stringify(S.state.rows));
    restoredReviewed = S.state.rows.filter((r) => r.reviewed).map((r) => r.id);
    restoredNote = S.state.note;
    const j2 = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0, wallNow: () => '' });
    j2.resume('s' + c, 'w', lastSeq, pairs);
    const off2 = S.on('op', (e) => j2.record(e));
    log.push('--reload--');
    randomSteps(2 + Math.floor(rnd() * 10), ids, log);
    off2();
    events = [...before, ...j2.pending];
  }
  cases.push({
    case: c, log, proposal, events,
    events_before_reload: before, last_seq: lastSeq,
    restored_rows: restoredRows, restored_reviewed: restoredReviewed, restored_note: restoredNote,
    rows: JSON.parse(JSON.stringify(S.state.rows)),
    note: S.state.note,
    reviewed_ids: S.state.rows.filter((r) => r.reviewed).map((r) => r.id),
  });
}
process.stdout.write(JSON.stringify(cases));
