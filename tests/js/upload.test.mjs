// The chunked upload client (app/static/js/upload.js) against an in-memory
// server that keeps the protocol of app/batch/uploads.py, with lost
// replies, dropped requests and server errors injected (batch design §5).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

const {
  uploadFile, importFiles, fingerprint, sha256Hex, relPathOf, batchNameFor, UploadError,
  RETRY_BUDGET_MS, MAX_WAIT_MS, recallDraft, rememberDraft, forgetDraft, forgetDraftsOf,
  isImportable, ACCEPT, paddedLength,
} = await import('../../app/static/js/upload.js');

const bytes = (n, seed = 7) => Uint8Array.from({ length: n }, (_, i) => (i * seed + 3) % 251);
function fileOf(data, name = 'a.pdf', relative = '') {
  const f = new File([data], name, { lastModified: 1 });
  if (relative) Object.defineProperty(f, 'webkitRelativePath', { value: relative });
  return f;
}
const noSleep = async () => {};
const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
const garbled = (status) => ({ ok: status < 400, status,
  json: async () => { throw new SyntaxError('truncated'); } });

// The server's rules: a chunk only at the offset it holds (else 409 + that
// offset); complete only with every byte; the same unfinished file resumes.
// `fault` may return 'drop-before', 'drop-after', a status number, or a
// whole reply object (returned as is, nothing written).
function fakeServer({ fault = () => null, onComplete = null } = {}) {
  const uploads = new Map();
  const calls = [];
  let n = 0;
  let written = 0;
  async function fetch(url, { method, body } = {}) {
    const u = new URL(url, 'http://sindri');
    calls.push({ method, path: u.pathname, offset: u.searchParams.get('offset') });
    if (method === 'POST' && u.pathname === '/api/uploads') {
      const req = JSON.parse(body);
      if (!/\.(pdf|zip)$/i.test(req.name)) {
        return json(400, { detail: 'only PDF and ZIP files can be imported' });
      }
      for (const [id, up] of uploads) {
        if (up.state === 'receiving' && up.batch_id === req.batch_id && up.name === req.name
            && up.fingerprint === req.fingerprint && up.rel_path === req.rel_path
            && up.size === req.size
            && (req.last_modified == null || up.last_modified === req.last_modified)) {
          return json(200, { upload_id: id, received: up.received, state: up.state, resumed: true });
        }
      }
      const id = `u${++n}`;
      uploads.set(id, { ...req, received: 0, state: 'receiving', bytes: new Uint8Array(req.size) });
      return json(200, { upload_id: id, received: 0, state: 'receiving', resumed: false });
    }
    let m = /^\/api\/uploads\/(\w+)$/.exec(u.pathname);
    if (method === 'PUT' && m) {
      const up = uploads.get(m[1]);
      const offset = Number(u.searchParams.get('offset'));
      const f = fault({ offset, up, puts: calls.filter((c) => c.method === 'PUT').length });
      if (f === 'drop-before') throw new TypeError('network down');
      if (typeof f === 'number') return json(f, { detail: 'injected' });
      if (f && typeof f === 'object') return f;
      if (up.state !== 'receiving' || offset !== up.received) {
        return json(409, { detail: 'offset does not match what the server holds',
                           received: up.received, state: up.state });
      }
      const data = new Uint8Array(await body.arrayBuffer());
      up.bytes.set(data, offset);
      up.received += data.length;
      written += data.length;
      if (f === 'drop-after') throw new TypeError('reply lost');
      return json(200, { received: up.received });
    }
    m = /^\/api\/uploads\/(\w+)\/complete$/.exec(u.pathname);
    if (method === 'POST' && m) {
      const up = uploads.get(m[1]);
      const forced = onComplete && onComplete(up);
      if (forced) return forced;
      if (up.received !== up.size) {
        return json(409, { detail: 'the upload is not complete', received: up.received, state: up.state });
      }
      up.state = 'complete';
      return json(200, { upload_id: m[1], state: 'complete',
                         documents: [{ id: `d-${m[1]}`, file_name: up.name }], ignored: [],
                         ignored_total: 0, ignored_by_reason: {} });
    }
    return json(404, { detail: 'no such route' });
  }
  return { fetch, uploads, calls, get written() { return written; } };
}

const puts = (server) => server.calls.filter((c) => c.method === 'PUT').map((c) => Number(c.offset));

test('sends the file in chunks at the server offset and completes', async () => {
  const data = bytes(300);
  const server = fakeServer();
  const seen = [];
  const done = await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch,
    sleep: noSleep, chunkSize: 64, onProgress: (sent, total) => seen.push([sent, total]) });
  assert.equal(done.state, 'complete');
  assert.equal(done.documents[0].file_name, 'a.pdf');
  assert.deepEqual(puts(server), [0, 64, 128, 192, 256]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
  assert.deepEqual(seen.at(-1), [300, 300]);
});

test('a lost reply is resynced from the 409, so no byte lands twice', async () => {
  const data = bytes(300);
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 ? 'drop-after' : null) });
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 64, 64, 128, 192, 256]);
  assert.equal(server.written, 300);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('a request that never arrived is sent again at the same offset', async () => {
  const data = bytes(300);
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 ? 'drop-before' : null) });
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 64, 64, 128, 192, 256]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('server errors are retried with growing waits', async () => {
  const waits = [];
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 || k === 3 ? 500 : null) });
  await uploadFile({ file: fileOf(bytes(200)), batchId: 'b', fetch: server.fetch, chunkSize: 64,
    sleep: async (ms) => { waits.push(ms); } });
  assert.deepEqual(waits, [500, 1000]);
  assert.equal(server.uploads.get('u1').state, 'complete');
});

test('a timeout (408) and a throttle (429) are retried, not fatal', async () => {
  const waits = [];
  const server = fakeServer({ fault: ({ puts: k }) => ({ 2: 408, 3: 429 })[k] ?? null });
  await uploadFile({ file: fileOf(bytes(200)), batchId: 'b', fetch: server.fetch, chunkSize: 64,
    sleep: async (ms) => { waits.push(ms); } });
  assert.deepEqual(waits, [500, 1000]);
  assert.deepEqual(server.uploads.get('u1').bytes, bytes(200));
});

test('a full disk stops at once, without retrying', async () => {
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 ? 507 : null) });
  await assert.rejects(
    uploadFile({ file: fileOf(bytes(200)), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 }),
    (err) => err instanceof UploadError && err.status === 507 && err.fatal);
  assert.deepEqual(puts(server), [0, 64]);
});

for (const status of [400, 404, 413, 422]) {
  test(`a ${status} on a chunk is fatal and not retried`, async () => {
    const server = fakeServer({ fault: ({ puts: k }) => (k === 2 ? status : null) });
    await assert.rejects(
      uploadFile({ file: fileOf(bytes(200)), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 }),
      (err) => err instanceof UploadError && err.status === status && err.fatal
        && err.message === 'injected');
    assert.deepEqual(puts(server), [0, 64]);
  });
}

test("a refused request reports the server's sentence and is not retried", async () => {
  const server = fakeServer();
  await assert.rejects(
    uploadFile({ file: fileOf(bytes(10), 'notes.exe'), batchId: 'b', fetch: server.fetch, sleep: noSleep }),
    (err) => err.fatal && err.status === 400 && err.message === 'only PDF and ZIP files can be imported');
  assert.equal(server.calls.length, 1);
});

test('it keeps retrying for the time budget, then gives up -- but not for good', async () => {
  // A fake clock that the waits advance: the budget is time, not a count,
  // so a server restart taking minutes is ridden out.
  let clock = 0;
  const waits = [];
  const sleep = async (ms) => { waits.push(ms); clock += ms; };
  let down = true;
  const server = fakeServer({ fault: ({ puts: k }) => (down && k >= 2 ? 'drop-before' : null) });
  const file = fileOf(bytes(200));
  await assert.rejects(
    uploadFile({ file, batchId: 'b', fetch: server.fetch, sleep, now: () => clock, chunkSize: 64 }),
    (err) => err instanceof UploadError && !err.fatal);
  assert.ok(clock >= RETRY_BUDGET_MS && clock < RETRY_BUDGET_MS + MAX_WAIT_MS, `${clock}`);
  assert.equal(Math.max(...waits), MAX_WAIT_MS);
  down = false;
  const before = puts(server).length;
  await uploadFile({ file, batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.equal(puts(server)[before], 64);           // resumed, not restarted
  assert.deepEqual(server.uploads.get('u1').bytes, bytes(200));
});

test('cancel during a backoff wait ends the wait, not after it', async () => {
  // The default sleep: a 500 forces a 500 ms wait, cancelled after ~10 ms.
  const ctl = new AbortController();
  const server = fakeServer({ fault: ({ puts: k }) => (k >= 2 ? 500 : null) });
  const t0 = Date.now();
  setTimeout(() => ctl.abort(), 10);
  await assert.rejects(
    uploadFile({ file: fileOf(bytes(200)), batchId: 'b', fetch: server.fetch, chunkSize: 64,
      signal: ctl.signal }),
    (err) => err.fatal && err.message === 'upload cancelled');
  assert.ok(Date.now() - t0 < 400, `${Date.now() - t0} ms`);
});

test('a reply whose body cannot be read is treated as lost, and resynced', async () => {
  const data = bytes(200);
  const server = fakeServer();
  let mangled = false;
  const fetch = async (url, init) => {
    const res = await server.fetch(url, init);
    if (init.method === 'PUT' && !mangled) {
      mangled = true;                                  // applied, but the reply is garbage
      return garbled(200);
    }
    return res;
  };
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 0, 64, 128, 192]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('a 409 whose body cannot be read is a lost reply too, not a refusal', async () => {
  const data = bytes(200);
  const server = fakeServer({ fault: ({ puts: k }) => (k === 2 ? garbled(409) : null) });
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 64, 64, 128, 192]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('a 200 without a usable offset is a lost reply, never a guess', async () => {
  const data = bytes(200);
  const server = fakeServer();
  let mangled = false;
  const fetch = async (url, init) => {
    const res = await server.fetch(url, init);
    if (init.method === 'PUT' && !mangled) {
      mangled = true;
      return json(200, { received: 'lots' });
    }
    return res;
  };
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 0, 64, 128, 192]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test("the server's offset is followed even when it goes DOWN", async () => {
  // The server trusts bytes on disk, not its count: after a cut-short part
  // file it answers a smaller `received`, and the client resends from there.
  const data = bytes(300);
  let cut = false;
  const server = fakeServer({ fault: ({ puts: k, up }) => {
    if (k === 4 && !cut) { cut = true; up.received = 64; }
    return null;
  } });
  await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [0, 64, 128, 192, 64, 128, 192, 256]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('a closed tab resumes: the same file finds its unfinished upload and sends only the rest', async () => {
  const data = bytes(300);
  const file = fileOf(data, 'a.pdf', 'Kunde/a.pdf');
  const server = fakeServer();
  const held = new Uint8Array(300);
  held.set(data.slice(0, 128));
  server.uploads.set('u9', { batch_id: 'b', name: 'a.pdf', size: 300, rel_path: 'Kunde',
    last_modified: 1, fingerprint: await fingerprint(file), received: 128, state: 'receiving',
    bytes: held });
  await uploadFile({ file, batchId: 'b', fetch: server.fetch, sleep: noSleep, chunkSize: 64 });
  assert.deepEqual(puts(server), [128, 192, 256]);
  assert.deepEqual(server.uploads.get('u9').bytes, data);
});

test('a completion refused for missing bytes sends them and completes', async () => {
  const data = bytes(200);
  let lost = false;
  const server = fakeServer({ onComplete: (up) => {
    if (lost) return null;
    lost = true;
    up.received = 64;                    // the server's part file was cut short
    return json(409, { detail: 'the server holds less of this file than it counted',
                       received: 64, state: 'receiving' });
  } });
  const done = await uploadFile({ file: fileOf(data), batchId: 'b', fetch: server.fetch,
    sleep: noSleep, chunkSize: 64 });
  assert.equal(done.state, 'complete');
  assert.deepEqual(puts(server), [0, 64, 128, 192, 64, 128, 192]);
  assert.deepEqual(server.uploads.get('u1').bytes, data);
});

test('a completion still in progress elsewhere is waited for, not counted as lost data', async () => {
  // 'completing' is a completion that has not finished (or a crash the
  // server's restart resets); its stored result is what a retry gets.
  const waits = [];
  let busy = 5;
  const server = fakeServer({ onComplete: (up) => (busy-- > 0
    ? json(409, { detail: 'the upload is not complete', received: up.size, state: 'completing' })
    : null) });
  const done = await uploadFile({ file: fileOf(bytes(100)), batchId: 'b', fetch: server.fetch,
    sleep: async (ms) => { waits.push(ms); }, chunkSize: 64 });
  assert.equal(done.state, 'complete');
  assert.equal(waits.length, 5);
  assert.deepEqual(puts(server), [0, 64]);
});

test('a lost completion reply is retried and answered with the stored result', async () => {
  const server = fakeServer();
  let dropped = false;
  const fetch = async (url, init) => {
    const res = await server.fetch(url, init);
    if (url.endsWith('/complete') && !dropped) { dropped = true; throw new TypeError('reply lost'); }
    return res;
  };
  // The fake answers a repeated completion from scratch; it is idempotent.
  const done = await uploadFile({ file: fileOf(bytes(100)), batchId: 'b', fetch, sleep: noSleep, chunkSize: 64 });
  assert.equal(done.state, 'complete');
});

test('the ignored-entry counts of a ZIP completion are passed through whole', async () => {
  const server = fakeServer({ onComplete: (up) => {
    if (up.received !== up.size) return null;
    return json(200, { upload_id: 'u1', state: 'complete', documents: [],
      ignored: [{ name: 'x.txt', reason: 'not_a_pdf' }], ignored_total: 1234,
      ignored_by_reason: { not_a_pdf: 1200, unreadable_entry: 34 } });
  } });
  const [r] = await importFiles([fileOf(bytes(10), 'Lieferung.zip')],
    { batchId: 'b', fetch: server.fetch, sleep: noSleep });
  assert.equal(r.ok, true);
  assert.equal(r.ignored_total, 1234);
  assert.deepEqual(r.ignored_by_reason, { not_a_pdf: 1200, unreadable_entry: 34 });
  assert.equal(r.ignored.length, 1);
});

test('cancel stops before the next request', async () => {
  const ctl = new AbortController();
  const server = fakeServer();
  await assert.rejects(
    uploadFile({ file: fileOf(bytes(300)), batchId: 'b', fetch: server.fetch, sleep: noSleep,
      chunkSize: 64, signal: ctl.signal, onProgress: (sent) => { if (sent >= 64) ctl.abort(); } }),
    (err) => err.fatal && err.message === 'upload cancelled');
  assert.deepEqual(puts(server), [0]);
});

test('the fingerprint is SHA-256 over the size, the first and the last MiB', async () => {
  const MiB = 1024 * 1024;
  const data = bytes(3 * MiB);
  const expected = createHash('sha256').update(Buffer.from(`${data.length}:`))
    .update(data.subarray(0, MiB)).update(data.subarray(2 * MiB)).digest('hex');
  assert.equal(await fingerprint(fileOf(data)), expected);
});

test('without crypto.subtle (no secure context) the fingerprint is the same, in plain JS', async () => {
  // Hosting is undecided (decision 23); browsers hide crypto.subtle outside
  // https and localhost.
  const enc = new TextEncoder();
  assert.equal(sha256Hex(enc.encode('abc')),
    'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(sha256Hex(new Uint8Array(0)),
    'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  for (const n of [55, 56, 64, 1000, 3 * 1024 * 1024]) {
    assert.equal(sha256Hex(bytes(n)), createHash('sha256').update(bytes(n)).digest('hex'), `${n}`);
    const file = fileOf(bytes(n));
    // null, not undefined: undefined would select the default (subtle) again.
    assert.equal(await fingerprint(file, null), await fingerprint(file, globalThis.crypto.subtle), `${n}`);
  }
});

test('with crypto.subtle undefined in the page, the default path falls back too', async () => {
  const file = fileOf(bytes(1000));
  const expected = await fingerprint(file, globalThis.crypto.subtle);
  const desc = Object.getOwnPropertyDescriptor(globalThis, 'crypto');
  Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true, writable: true });
  try {
    assert.equal(await fingerprint(file), expected);
  } finally {
    Object.defineProperty(globalThis, 'crypto', desc);
  }
});

test('the draft of an interrupted file is remembered per file, and storage faults are harmless', () => {
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v),
                    removeItem: (k) => store.delete(k) };
  const a = fileOf(bytes(10), 'a.pdf');
  const b = fileOf(bytes(11), 'a.pdf');          // same name, different size: another file
  rememberDraft(storage, a, 'batch-1');
  assert.equal(recallDraft(storage, a), 'batch-1');
  assert.equal(recallDraft(storage, b), null);
  forgetDraft(storage, a);
  assert.equal(recallDraft(storage, a), null);
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('full'); },
                   removeItem() { throw new Error('denied'); } };
  rememberDraft(broken, a, 'batch-1');
  forgetDraft(broken, a);
  assert.equal(recallDraft(broken, a), null);
  assert.equal(recallDraft(undefined, a), null);
});

test('every remembered file of one draft is forgotten by the draft id alone', () => {
  // A drawing reached by its link has no File to forget by; Start or Cancel
  // of its draft must still clear the memory pointing at it.
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)),
                    removeItem: (k) => store.delete(k), key: (i) => [...store.keys()][i] ?? null,
                    get length() { return store.size; } };
  const a = fileOf(bytes(10), 'a.pdf');
  const b = fileOf(bytes(11), 'b.pdf');
  const c = fileOf(bytes(12), 'c.pdf');
  rememberDraft(storage, a, 'batch-1');
  rememberDraft(storage, b, 'batch-1');
  rememberDraft(storage, c, 'batch-2');
  storage.setItem('other.app', 'batch-1');            // not ours: never touched
  forgetDraftsOf(storage, 'batch-1');
  assert.equal(recallDraft(storage, a), null);
  assert.equal(recallDraft(storage, b), null);
  assert.equal(recallDraft(storage, c), 'batch-2');
  assert.equal(storage.getItem('other.app'), 'batch-1');
  const broken = { get length() { throw new Error('denied'); }, key() { throw new Error('denied'); },
                   getItem() { throw new Error('denied'); }, removeItem() { throw new Error('denied'); } };
  forgetDraftsOf(broken, 'batch-1');
  forgetDraftsOf(null, 'batch-1');
  forgetDraftsOf(storage, null);
  assert.equal(recallDraft(storage, c), 'batch-2');
});

test('the fingerprint is a key for resuming, not a content hash', async () => {
  // It tells apart files that differ at either end, and deliberately not
  // ones that differ only in the middle: duplicates are found by the
  // server's full-file SHA-256, never by this.
  const MiB = 1024 * 1024;
  const a = bytes(3 * MiB);
  const end = a.slice();
  end[end.length - 1] ^= 1;
  const middle = a.slice();
  middle[Math.floor(1.5 * MiB)] ^= 1;
  assert.notEqual(await fingerprint(fileOf(a)), await fingerprint(fileOf(end)));
  assert.equal(await fingerprint(fileOf(a)), await fingerprint(fileOf(middle)));
});

test('the folder of a folder selection, and the batch name of decision 27', () => {
  assert.equal(relPathOf(fileOf(bytes(1), 'a.pdf', 'Kunde/Teile/a.pdf')), 'Kunde/Teile');
  assert.equal(relPathOf(fileOf(bytes(1))), '');
  assert.equal(batchNameFor([fileOf(bytes(1), 'Lieferung 12.ZIP')]), 'Lieferung 12');
  assert.equal(batchNameFor([fileOf(bytes(1), 'a.pdf', 'Kunde/a.pdf'),
                             fileOf(bytes(1), 'b.pdf', 'Kunde/x/b.pdf')]), 'Kunde');
  const at = new Date(2026, 9, 7, 9, 5);
  assert.equal(batchNameFor([fileOf(bytes(1))], at), 'Import 2026-10-07 09:05');
  assert.equal(batchNameFor([fileOf(bytes(1), 'a.zip'), fileOf(bytes(1))], at), 'Import 2026-10-07 09:05');
});

test('the picker accepts PDFs and ZIPs, in any case, and nothing else', () => {
  assert.match(ACCEPT, /\.pdf/);
  assert.match(ACCEPT, /\.zip/);
  for (const name of ['a.pdf', 'B.PDF', 'Lieferung.zip', 'x.Zip']) assert.equal(isImportable(name), true, name);
  for (const name of ['a.exe', 'pdf', '.pdf', 'a.pdf.txt', '']) assert.equal(isImportable(name), false, name);
});

test('the client never logs: console output would carry file names', () => {
  const src = readFileSync(new URL('../../app/static/js/upload.js', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /console\./);
});

test('importFiles runs at most three uploads at once and one failure stops none of the others', async () => {
  const server = fakeServer();
  let active = 0;
  let peak = 0;
  const fetch = async (url, init) => {
    const u = new URL(url, 'http://sindri');
    const starting = init.method === 'POST' && u.pathname === '/api/uploads';
    if (starting) { active += 1; peak = Math.max(peak, active); }
    await new Promise((r) => setImmediate(r));       // let the other lanes run
    const res = await server.fetch(url, init);
    if ((starting && !res.ok) || (u.pathname.endsWith('/complete') && res.ok)) active -= 1;
    return res;
  };
  const files = [0, 1, 2, 3, 4, 5].map((i) => fileOf(bytes(100, i + 2), i === 2 ? 'bad.exe' : `f${i}.pdf`));
  const results = await importFiles(files, { batchId: 'b', fetch, sleep: noSleep, chunkSize: 32 });
  assert.deepEqual(results.map((r) => r.ok), [true, true, false, true, true, true]);
  assert.equal(results[2].error.status, 400);
  assert.ok(peak >= 2 && peak <= 3, `peak ${peak}`);
});

test('an unreachable server ends an import in one budget, not one budget per file', async () => {
  // 300 queued files x 10 minutes each would be hours. Once one file has
  // given up on the server, files not yet started are not attempted -- and
  // that is not fatal: selecting them again resumes.
  // The lanes wait CONCURRENTLY, so the fake clock is a timer queue: each
  // wait wakes at its own deadline, earliest first. A clock every wait
  // simply added to would count three lanes' waits three times over.
  let clock = 0;
  const timers = [];
  let ticking = false;
  const tick = () => {
    if (ticking) return;
    ticking = true;
    setImmediate(() => {                       // after every lane has reached its wait
      ticking = false;
      if (!timers.length) return;
      timers.sort((a, b) => a.at - b.at);
      const t = timers.shift();
      clock = Math.max(clock, t.at);
      t.wake();
      tick();
    });
  };
  const sleep = (ms) => new Promise((wake) => { timers.push({ at: clock + ms, wake }); tick(); });
  const started = [];
  const fetch = async (url, init) => {
    if (init.method === 'POST' && url === '/api/uploads') started.push(JSON.parse(init.body).name);
    throw new TypeError('network down');
  };
  const files = [0, 1, 2, 3, 4, 5].map((i) => fileOf(bytes(50), `f${i}.pdf`));
  const results = await importFiles(files, { batchId: 'b', fetch, sleep, now: () => clock });
  // One budget plus the last wait that overshoots it, plus a second of slack.
  assert.ok(clock < RETRY_BUDGET_MS + MAX_WAIT_MS + 1000, `${clock}`);
  assert.deepEqual([...new Set(started)].sort(), ['f0.pdf', 'f1.pdf', 'f2.pdf']);
  assert.ok(results.every((r) => !r.ok && r.error instanceof UploadError && !r.error.fatal));
  assert.deepEqual(results.slice(3).map((r) => r.error.message),
    Array(3).fill('not attempted: the server could not be reached'));
});

test('a refusal of one file does not stop the files after it from starting', async () => {
  const server = fakeServer();
  const files = [fileOf(bytes(10), 'bad.exe'), fileOf(bytes(10), 'b.pdf'), fileOf(bytes(10), 'c.pdf')];
  const results = await importFiles(files, { batchId: 'b', fetch: server.fetch, sleep: noSleep, parallel: 1 });
  assert.deepEqual(results.map((r) => r.ok), [false, true, true]);
});

// A File whose reads are counted, or fail.
function spyFile(data, name, { fail = false } = {}) {
  const real = fileOf(data, name);
  const spy = { reads: 0, name, size: real.size, lastModified: 1, webkitRelativePath: '' };
  spy.slice = (...a) => {
    spy.reads += 1;
    if (fail) {
      return { arrayBuffer: async () => { const e = new Error('gone'); e.name = 'NotReadableError'; throw e; } };
    }
    return real.slice(...a);
  };
  return spy;
}

test('cancel leaves the files not yet started unread', async () => {
  const ctl = new AbortController();
  const server = fakeServer();
  const files = [0, 1, 2].map((i) => spyFile(bytes(100), `f${i}.pdf`));
  const results = await importFiles(files, { batchId: 'b', fetch: server.fetch, sleep: noSleep,
    parallel: 1, signal: ctl.signal, onFile: (i, ev) => { if (ev.done) ctl.abort(); } });
  assert.equal(results[0].ok, true);
  for (const r of results.slice(1)) assert.equal(r.error.message, 'upload cancelled');
  assert.deepEqual(files.map((f) => f.reads > 0), [true, false, false]);
});

test('an already cancelled upload does not even fingerprint the file', async () => {
  const ctl = new AbortController();
  ctl.abort();
  const file = spyFile(bytes(100), 'a.pdf');
  await assert.rejects(uploadFile({ file, batchId: 'b', fetch: fakeServer().fetch, signal: ctl.signal }),
    (err) => err.fatal && err.message === 'upload cancelled');
  assert.equal(file.reads, 0);
});

test('a file that cannot be read is a fatal error of its own, not a network fault', async () => {
  const server = fakeServer();
  await assert.rejects(
    uploadFile({ file: spyFile(bytes(100), 'a.pdf', { fail: true }), batchId: 'b', fetch: server.fetch,
      sleep: noSleep }),
    (err) => err instanceof UploadError && err.fatal && err.message === 'the file could not be read'
      // Named, so the caller can tell "this file, now" from a refusal of the
      // draft: a remembered draft's partial bytes are kept for it.
      && err.reason === 'file_unreadable');
  assert.equal(server.calls.length, 0);
});

test('an attempt that hangs is timed out and counted as a dropped request', async () => {
  // The injected timer stands in for AbortSignal.timeout. A timeout is not
  // the user's cancel: the request is sent again at the same offset.
  const server = fakeServer();
  const timers = [];
  const timeout = () => { const c = new AbortController(); timers.push(c); return c.signal; };
  let hung = false;
  const fetch = (url, init) => {
    if (init.method === 'PUT' && !hung) {
      hung = true;
      return new Promise((_, reject) => init.signal.addEventListener('abort',
        () => reject(new DOMException('timed out', 'TimeoutError'))));
    }
    return server.fetch(url, init);
  };
  const run = uploadFile({ file: fileOf(bytes(100)), batchId: 'b', fetch, sleep: noSleep,
    chunkSize: 64, timeout });
  while (!hung) await new Promise((r) => setImmediate(r));   // the fingerprint runs first
  timers.at(-1).abort();                       // the hung attempt's own timer fires
  const done = await run;
  assert.equal(done.state, 'complete');
  assert.deepEqual(puts(server), [0, 64]);
  assert.deepEqual(server.uploads.get('u1').bytes, bytes(100));
});

test('a completion still running at the end of the budget says so', async () => {
  let clock = 0;
  const server = fakeServer({ onComplete: (up) =>
    json(409, { detail: 'the upload is not complete', received: up.size, state: 'completing' }) });
  await assert.rejects(
    uploadFile({ file: fileOf(bytes(100)), batchId: 'b', fetch: server.fetch, chunkSize: 64,
      sleep: async (ms) => { clock += ms; }, now: () => clock }),
    (err) => !err.fatal && err.status === 409 && err.message === 'the server is still finishing this file');
});

test('isImportable reads the name as the server does: NFC, surrounding spaces stripped', () => {
  assert.equal(isImportable('a.pdf '), true);
  assert.equal(isImportable('  b.ZIP\t'), true);
  // No precomposed f-acute exists, so NFC keeps the mark in the extension
  // and the server's upload_kind refuses it too.
  assert.equal(isImportable('a.pdf́'), false);
});

test('the SHA-256 padding length is plain arithmetic, so it never wraps at 2 GiB', () => {
  // The fingerprint hashes at most 2 MiB today; a 32-bit shift would turn
  // any input of 2 GiB or more into a negative array length.
  assert.equal(paddedLength(0), 64);
  assert.equal(paddedLength(55), 64);
  assert.equal(paddedLength(56), 128);
  assert.equal(paddedLength(2 ** 31), 2 ** 31 + 64);
  assert.equal(paddedLength(2 ** 32 + 100), 2 ** 32 + 128);
});

test('a slow link still imports: a chunk is given time in proportion to its bytes', async () => {
  // Decision 29: no upload limits. A fixed two minutes would need 70 KB/s
  // for an 8 MiB chunk, and a slow factory uplink would time out every PUT
  // forever. The floor is 16 KiB/s: 8 MiB gets 512 s.
  const MiB = 1024 * 1024;
  const server = fakeServer();
  let clock = 0;
  const timers = [];
  const timeout = (ms) => { const c = new AbortController(); timers.push({ at: clock + ms, c }); return c.signal; };
  const advance = (t) => { clock = t; for (const x of timers) if (x.at <= clock) x.c.abort(); };
  let hung = 0;
  let rejected = false;
  const fetch = (url, init) => {
    if (init.method === 'PUT' && hung === 0) {
      hung += 1;
      return new Promise((_, reject) => init.signal.addEventListener('abort', () => {
        rejected = true;
        reject(new DOMException('timed out', 'TimeoutError'));
      }));
    }
    return server.fetch(url, init);
  };
  const run = uploadFile({ file: fileOf(bytes(8 * MiB)), batchId: 'b', fetch, sleep: noSleep,
    now: () => clock, timeout });
  while (!hung) await new Promise((r) => setImmediate(r));
  advance(121 * 1000);
  await new Promise((r) => setImmediate(r));
  assert.equal(rejected, false, 'an 8 MiB chunk is not timed out at 121 s');
  advance((8 * MiB) / (16 * 1024) * 1000 + 1000);
  const done = await run;
  assert.equal(rejected, true);
  assert.equal(done.state, 'complete');
  assert.deepEqual(puts(server), [0]);
});

test('a stopped import says why: the first give-up reason, not a guess', async () => {
  // A server answering 503 for ten minutes is reachable; the files it
  // stopped must not be told it could not be reached.
  let clock = 0;
  const timers = [];
  let ticking = false;
  const tick = () => {
    if (ticking) return;
    ticking = true;
    setImmediate(() => {
      ticking = false;
      if (!timers.length) return;
      timers.sort((a, b) => a.at - b.at);
      const t = timers.shift();
      clock = Math.max(clock, t.at);
      t.wake();
      tick();
    });
  };
  const sleep = (ms) => new Promise((wake) => { timers.push({ at: clock + ms, wake }); tick(); });
  const fetch = async () => json(503, { detail: 'unavailable' });
  const files = [0, 1, 2, 3, 4].map((i) => fileOf(bytes(50), `f${i}.pdf`));
  const results = await importFiles(files, { batchId: 'b', fetch, sleep, now: () => clock });
  assert.ok(results.every((r) => !r.ok && !r.error.fatal));
  assert.deepEqual(results.slice(3).map((r) => r.error.message),
    Array(2).fill('not attempted: server error (503)'));
});

test('one flaky file does not stop a healthy import', async () => {
  // A file moved during the import, or one the server always fails, runs
  // out its own budget while the other lanes keep landing bytes. That is
  // the file's fault, not the server's: the files after it still start.
  let clock = 0;
  const timers = [];
  let ticking = false;
  const tick = () => {
    if (ticking) return;
    ticking = true;
    setImmediate(() => {
      ticking = false;
      if (!timers.length) return;
      timers.sort((a, b) => a.at - b.at);
      const t = timers.shift();
      clock = Math.max(clock, t.at);
      t.wake();
      tick();
    });
  };
  const sleep = (ms) => new Promise((wake) => { timers.push({ at: clock + ms, wake }); tick(); });
  const server = fakeServer();
  const flaky = fileOf(bytes(50), 'f0.pdf');
  const fetch = async (url, init) => {
    const m = /^\/api\/uploads\/(\w+)\?/.exec(url);
    if (init.method === 'PUT' && m) {
      if (server.uploads.get(m[1]).name === flaky.name) throw new TypeError('network down');
      await sleep(400 * 1000);                     // a slow but healthy link
    }
    return server.fetch(url, init);
  };
  const files = [flaky, ...[1, 2, 3].map((i) => fileOf(bytes(50), `f${i}.pdf`))];
  const results = await importFiles(files, { batchId: 'b', fetch, sleep, now: () => clock, parallel: 2 });
  assert.deepEqual(results.map((r) => r.ok), [false, true, true, true]);
  assert.equal(results[0].error.fatal, false);
  assert.equal(results[0].error.message, 'the server could not be reached');
});
