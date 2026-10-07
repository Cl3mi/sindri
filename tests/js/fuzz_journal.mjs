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
for (let c = 0; c < N; c++) {
  const ids = ['a', 'b', 'c', 'd', 'e'];
  const proposal = ids.map((i) => row(i));
  S.setSession({ session_id: 's' + c, image_url: '', rows: JSON.parse(JSON.stringify(proposal)), notes: null });
  const j = createJournal({ send: async () => ({ contiguous: 0 }), now: () => 0, wallNow: () => '' });
  j.start('s' + c, 'w');
  const off = S.on('op', (e) => j.record(e));
  const steps = 2 + Math.floor(rnd() * 14);
  const log = [];
  for (let k = 0; k < steps; k++) {
    const anyIds = [...new Set([...ids, ...S.state.rows.map((r) => r.id), 'zz'])];
    const r = rnd();
    let label;
    if (r < 0.18) { S.undo(); label = 'undo'; }
    else if (r < 0.30) { S.redo(); label = 'redo'; }
    else if (r < 0.45) {
      const id = pick(anyIds); const f = pick(['char_type', 'nominal', 'upper_tol', 'lower_tol']);
      const v = pick(['', '1', '2', 'Diameter', '+0.1']);
      S.apply(S.opEditCell(id, f, v)); label = `edit ${id}.${f}=${v}`;
    } else if (r < 0.55) {
      const id = pick(anyIds); S.apply(S.opMoveRow(id, [Math.round(rnd() * 100), 7])); label = `move ${id}`;
    } else if (r < 0.65) {
      const id = pick(anyIds); S.apply(S.opDeleteRow(id)); label = `del ${id}`;
    } else if (r < 0.72) {
      const id = 'm' + (addN++);
      S.apply(S.opAddRow({ ...row(id), suggested: false, reviewed: false, source: 'manual' }));
      label = `add ${id}`;
    } else if (r < 0.80) {
      const sel = anyIds.filter(() => rnd() < 0.4); S.apply(S.opConfirmSuggestions(sel)); label = `confirm ${sel}`;
    } else {
      const sel = anyIds.filter(() => rnd() < 0.4); const t = rnd() < 0.6;
      S.apply(S.opBulkReview(sel, t)); label = `${t ? 'accept' : 'unaccept'} ${sel}`;
    }
    log.push(label);
  }
  off();
  cases.push({
    case: c, log, proposal, events: j.pending,
    rows: JSON.parse(JSON.stringify(S.state.rows)),
    reviewed_ids: S.state.rows.filter((r) => r.reviewed).map((r) => r.id),
  });
}
process.stdout.write(JSON.stringify(cases));
