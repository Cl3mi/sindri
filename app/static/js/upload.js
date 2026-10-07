// Chunked, resumable upload client (batch design §2, decision 21).
//
// Pure on purpose: every request goes through an injected `fetch` and every
// wait through an injected `sleep`, so node drives it against an in-memory
// server with lost replies, dropped requests and server errors injected
// (tests/js/upload.test.mjs). The SERVER is the authority on the offset: a
// 409 carries the bytes it really holds, and the client continues from
// there -- never from what it believes it sent. That offset can go DOWN
// (the server trusts bytes on disk, not its own count), so nothing here
// assumes it only grows.
//
// Never logs: anything worth logging here would carry a client file name.

export const CHUNK_SIZE = 8 * 1024 * 1024;     // design §2: 8 MiB per PUT (server bound: 64 MiB)
export const FINGERPRINT_SPAN = 1024 * 1024;   // first and last MiB
export const PARALLEL = 3;                     // design §2: three uploads at once
// How long one request keeps retrying a dropped connection or a 5xx before
// giving up FOR NOW (non-fatal: the same file selected again resumes). Time,
// not a count: a server restart or a Wi-Fi hand-over takes minutes, and a
// count of quick retries would give up long before either is over.
export const RETRY_BUDGET_MS = 10 * 60 * 1000;
export const MAX_WAIT_MS = 20 * 1000;          // the longest single wait between retries

// What the server imports (app/batch/uploads.py upload_kind): the file
// picker's `accept`, and a check before a request is spent on a refusal.
export const ACCEPT = '.pdf,.zip,application/pdf,application/zip';

export function isImportable(name) {
  // As the server reads a name: NFC, surrounding whitespace stripped
  // (Uploads.create), and leading dots are not an extension separator
  // (os.path.splitext), so ".pdf" alone is a name with no extension.
  const clean = String(name || '').normalize('NFC').trim().replace(/^\.+/, '');
  return /\.(pdf|zip)$/i.test(clean);
}

export class UploadError extends Error {
  // `fatal`: retrying cannot help (refused, disk full, cancelled). Not
  // fatal: the network or server is down for now, and the same file
  // selected again resumes where the server stopped.
  constructor(message, { status = 0, fatal = false } = {}) {
    super(message);
    this.name = 'UploadError';
    this.status = status;
    this.fatal = fatal;
  }
}

// SHA-256 in plain JS, for when crypto.subtle does not exist: browsers
// offer it only in a secure context (https or localhost), and how Sindri is
// hosted is undecided (decision 23). Only the fingerprint uses it -- at most
// 2 MiB, a resume key, never a duplicate check -- so speed does not matter.
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

const rotr = (x, n) => (x >>> n) | (x << (32 - n));

// The message, the 0x80 marker, zeros, and the 64-bit bit length, padded to
// whole 64-byte blocks. Plain arithmetic, not `>> 6 << 6`: JS shifts are
// 32-bit, so from 2 GiB on a shift wraps to a negative length.
export const paddedLength = (n) => Math.ceil((n + 9) / 64) * 64;

export function sha256Hex(bytes) {
  const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                             0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const padded = new Uint8Array(paddedLength(bytes.length));
  padded.set(bytes);
  padded[bytes.length] = 0x80;
  const view = new DataView(padded.buffer);
  const bits = bytes.length * 8;
  view.setUint32(padded.length - 8, Math.floor(bits / 2 ** 32));
  view.setUint32(padded.length - 4, bits >>> 0);
  const w = new Uint32Array(64);
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + 4 * i);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = w[i - 16] + s0 + w[i - 7] + s1;            // Uint32Array wraps mod 2^32
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let i = 0; i < 64; i++) {
      const t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
  }
  return Array.from(h, (x) => x.toString(16).padStart(8, '0')).join('');
}

// SHA-256 over the size and the first and last MiB. A browser cannot hash a
// multi-GB file incrementally, and this is only the KEY that finds an
// unfinished upload of the same file again; duplicates are found by the
// server's full-file SHA-256, which never trusts this. `subtle` is read at
// call time, so a page without a secure context takes the plain-JS path.
export async function fingerprint(file, subtle = globalThis.crypto?.subtle) {
  let head;
  let tail;
  try {
    head = new Uint8Array(await file.slice(0, Math.min(file.size, FINGERPRINT_SPAN)).arrayBuffer());
    tail = new Uint8Array(await file.slice(Math.max(0, file.size - FINGERPRINT_SPAN), file.size).arrayBuffer());
  } catch {
    // NotReadableError and kin: the file was moved, deleted or changed
    // since it was selected. Retrying cannot help; selecting it again can.
    // (A read failure DURING the upload surfaces inside fetch instead, as
    // a rejected request -- indistinguishable there from a network drop,
    // so it is retried until the budget runs out, then reported non-fatal.)
    // `reason` names it: the draft is not at fault, so a caller keeps a
    // remembered draft's partial bytes for the next selection (importone.js).
    throw Object.assign(new UploadError('the file could not be read', { fatal: true }),
                        { reason: 'file_unreadable' });
  }
  const size = new TextEncoder().encode(`${file.size}:`);
  const all = new Uint8Array(size.length + head.length + tail.length);
  all.set(size, 0);
  all.set(head, size.length);
  all.set(tail, size.length + head.length);
  if (subtle) {
    try {
      const digest = new Uint8Array(await subtle.digest('SHA-256', all));
      return Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('');
    } catch { /* a subtle that refuses: the same digest in plain JS */ }
  }
  return sha256Hex(all);
}

// Remembering which draft an interrupted file was going into, so selecting
// the same file again -- after a failure, a reload or a closed tab -- resumes
// it there (decision 21; the server only resumes within the same draft).
// `storage` is localStorage in the browser, injected so node can test it;
// every access is guarded, since storage can be full or disabled.
const DRAFT_PREFIX = 'sindri.import:';
const draftKey = (file) => `${DRAFT_PREFIX}${file.name}:${file.size}:${file.lastModified}`;

export function recallDraft(storage, file) {
  try { return storage?.getItem(draftKey(file)) || null; } catch { return null; }
}

export function rememberDraft(storage, file, batchId) {
  try { storage?.setItem(draftKey(file), batchId); } catch { /* resume is a convenience */ }
}

export function forgetDraft(storage, file) {
  try { storage?.removeItem(draftKey(file)); } catch { /* nothing to undo */ }
}

// Every remembered file of one draft, by the draft id alone: a drawing
// reached by its link has no File to forget by, yet its Start or Cancel
// ends the draft. Keys are collected first -- removing while indexing
// would skip the key that slides into the freed slot.
export function forgetDraftsOf(storage, batchId) {
  if (!batchId) return;
  try {
    const mine = [];
    for (let i = 0; i < storage.length; i++) {
      const k = storage.key(i);
      if (k?.startsWith(DRAFT_PREFIX) && storage.getItem(k) === batchId) mine.push(k);
    }
    for (const k of mine) storage.removeItem(k);
  } catch { /* resume is a convenience; a stale memory falls back (importone.js) */ }
}

// A wait that ends early on cancel, so Cancel never sits out a 20 s backoff.
function abortableSleep(ms, signal) {
  return new Promise((resolve) => {
    if (signal?.aborted) { resolve(); return; }
    const onAbort = () => { clearTimeout(t); resolve(); };
    const t = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, ms);
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

// The folder a file came from in a folder selection (webkitdirectory), ''
// for a plain file. The server judges it again (app/batch/paths.py).
export function relPathOf(file) {
  const p = file.webkitRelativePath || '';
  const i = p.lastIndexOf('/');
  return i > 0 ? p.slice(0, i) : '';
}

// Decision 27: a single ZIP's name, a single folder's name, else the date
// and time. Editable afterwards (PATCH /api/batches/{id}); the server
// cleans it and may refuse it (400), so the caller keeps a fallback.
export function batchNameFor(files, now = new Date()) {
  if (files.length === 1 && /\.zip$/i.test(files[0].name)) return files[0].name.replace(/\.zip$/i, '');
  const tops = new Set(files.map((f) => (f.webkitRelativePath || '').split('/')[0]));
  if (files.length && tops.size === 1 && !tops.has('')) return [...tops][0];
  const pad = (n) => String(n).padStart(2, '0');
  return `Import ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} `
    + `${pad(now.getHours())}:${pad(now.getMinutes())}`;
}

const isCount = (x) => Number.isInteger(x) && x >= 0;
const cancelled = () => new UploadError('upload cancelled', { fatal: true });

// One attempt may not hang forever: a connection that died without a reset
// (a sleeping laptop, a NAT that forgot the flow) never rejects on its own,
// and the retry budget only runs between attempts. A timed-out attempt is a
// dropped request. The completion gets far longer: a ZIP with many entries
// is one long request, and timing it out would only queue a retry behind
// the same server lock.
export const ATTEMPT_TIMEOUT_MS = 2 * 60 * 1000;
export const COMPLETE_TIMEOUT_MS = 30 * 60 * 1000;
// A chunk's timer scales with its bytes, never below ATTEMPT_TIMEOUT_MS:
// decision 29 sets no upload limits, so a slow factory uplink must still
// import. A fixed two minutes would demand ~70 KB/s for an 8 MiB chunk and
// time out every PUT on a slower link, forever. 16 KiB/s is the floor a
// link must sustain: 8 MiB gets 512 s.
export const MIN_RATE_BYTES_PER_S = 16 * 1024;
export const chunkTimeoutMs = (n) => Math.max(ATTEMPT_TIMEOUT_MS, Math.ceil((n / MIN_RATE_BYTES_PER_S) * 1000));
const defaultTimeout = (ms) => (typeof AbortSignal.timeout === 'function' ? AbortSignal.timeout(ms) : null);

// The user's cancel and this attempt's timer, as one signal. Without
// AbortSignal.any (older browsers) only the user's cancel applies.
function attemptSignal(signal, timer) {
  const parts = [signal, timer].filter(Boolean);
  if (parts.length < 2) return parts[0];
  return typeof AbortSignal.any === 'function' ? AbortSignal.any(parts) : signal;
}

// Statuses that say "not now" rather than "no": a timeout and a throttle
// are retried like a 5xx. 507 (disk full) is a 5xx that retrying cannot fix.
const TRANSIENT_4XX = new Set([408, 429]);

// One request, retried on a dropped connection, a reply that cannot be read
// (or reads as nonsense), a 5xx, 408 or 429 -- with growing waits, for up to
// RETRY_BUDGET_MS. A 409 that says where the server stands is an ANSWER,
// not an error: it is returned for the caller to resync from. Any other
// 4xx (400, 404 -- the upload is gone --, 413, 422) and 507 are fatal.
async function request({ send, sleep, now, budgetMs, signal, timeout }, method, url, body, headers,
                       valid = () => true, timeoutMs = ATTEMPT_TIMEOUT_MS) {
  let failures = 0;
  let firstFailure = null;
  for (;;) {
    if (signal?.aborted) throw cancelled();
    let res = null;
    let stillFinishing = false;
    // A fresh timer per attempt; its firing aborts only this attempt. Only
    // the USER's signal means cancel -- a timeout is a dropped request.
    const attempt = attemptSignal(signal, timeout(timeoutMs));
    try {
      res = await send(url, { method, body, headers, signal: attempt });
    } catch {
      if (signal?.aborted) throw cancelled();
    }
    if (res) {
      let data = null;
      let readable = true;
      try { data = await res.json(); } catch {
        // Includes a timer firing mid-body; a cancel is still a cancel.
        if (signal?.aborted) throw cancelled();
        readable = false;
      }
      if (res.ok) {
        // A body cut off mid-reply, or one without the fields the protocol
        // promises, is a lost reply, not an answer: retry, and the
        // server's 409 / stored completion says what really happened.
        if (readable && data && valid(data)) return data;
      } else if (res.status === 409) {
        // Unreadable: lost too. 'completing': a completion still running
        // (or one a restart will reset) -- wait for it, its stored result
        // is what the retry gets.
        if (readable && data && isCount(data.received)) {
          if (data.state !== 'completing') return data;
          stillFinishing = true;
        } else if (readable) {
          throw new UploadError(typeof data?.detail === 'string' ? data.detail : 'upload refused (409)',
            { status: 409, fatal: true });
        }
      } else if (res.status === 507) {
        throw new UploadError('not enough free disk space on the server', { status: 507, fatal: true });
      } else if (res.status < 500 && !TRANSIENT_4XX.has(res.status)) {
        const msg = typeof data?.detail === 'string' ? data.detail : `upload refused (${res.status})`;
        throw new UploadError(msg, { status: res.status, fatal: true });
      }
    }
    failures += 1;
    if (firstFailure === null) firstFailure = now();
    if (now() - firstFailure >= budgetMs) {
      if (stillFinishing) {
        throw new UploadError('the server is still finishing this file', { status: 409 });
      }
      throw new UploadError(res ? `server error (${res.status})` : 'the server could not be reached',
        { status: res ? res.status : 0 });
    }
    await sleep(Math.min(MAX_WAIT_MS, 500 * 2 ** (failures - 1)), signal);
  }
}

// Upload one file into a draft batch; resolves the server's completion
// whole ({ upload_id, state: 'complete', documents, ignored, ignored_total,
// ignored_by_reason }) -- a ZIP's ignored list is capped at 1000 entries,
// so the counts are the truth and must reach the UI.
export async function uploadFile({
  file, batchId, relPath = relPathOf(file),
  fetch: send = (...a) => globalThis.fetch(...a),
  sleep = abortableSleep, now = () => Date.now(), budgetMs = RETRY_BUDGET_MS,
  chunkSize = CHUNK_SIZE, onProgress = () => {}, signal, timeout = defaultTimeout,
} = {}) {
  const call = (method, url, body, headers, valid, timeoutMs) =>
    request({ send, sleep, now, budgetMs, signal, timeout }, method, url, body, headers, valid, timeoutMs);
  // Before the fingerprint: a cancelled import must not read 2 MiB of
  // every file still queued behind it.
  if (signal?.aborted) throw cancelled();
  const key = await fingerprint(file);
  const created = await call('POST', '/api/uploads', JSON.stringify({
    batch_id: batchId, name: file.name, size: file.size,
    last_modified: Number.isFinite(file.lastModified) ? file.lastModified : null,
    rel_path: relPath, fingerprint: key,
  }), { 'Content-Type': 'application/json' },
  (r) => typeof r.upload_id === 'string' && isCount(r.received) && r.received <= file.size);
  const id = encodeURIComponent(created.upload_id);
  let received = created.received;
  onProgress(received, file.size);
  // A completion refused because the server holds less than it counted
  // sends the rest and tries again -- but not forever: a server that keeps
  // losing data is a fault to report.
  for (let resyncs = 0; resyncs <= 3; resyncs++) {
    while (received < file.size) {
      const end = Math.min(file.size, received + chunkSize);
      const r = await call('PUT', `/api/uploads/${id}?offset=${received}`,
        file.slice(received, end), { 'Content-Type': 'application/octet-stream' },
        (x) => isCount(x.received) && x.received <= file.size, chunkTimeoutMs(end - received));
      // 200 and 409 both say where the server now stands -- which may be
      // BEHIND where this client got to. After a lost reply the retry is a
      // 409 whose `received` already counts the chunk, so it is not sent
      // again.
      received = Math.min(r.received, file.size);
      onProgress(received, file.size);
    }
    const done = await call('POST', `/api/uploads/${id}/complete`, undefined, undefined,
      (x) => x.state === 'complete', COMPLETE_TIMEOUT_MS);
    if (done.state === 'complete') return done;
    received = Math.min(done.received, file.size);
    onProgress(received, file.size);
  }
  throw new UploadError('the server keeps losing this upload', { fatal: true });
}

// Upload many files, PARALLEL at a time. A file the server REFUSED never
// stops the others; each result is { ok: true, ...completion } or
// { ok: false, error }.
//
// But once one file has given up for now (a non-fatal error: its retry
// budget ran out on a dropped connection, a 5xx, or a completion still
// running), files not yet started are not attempted: the budget is per
// file, so 300 queued files would otherwise each sit out ten minutes --
// hours before the import ends. Their error is non-fatal too, so selecting
// them again resumes, and it carries the FIRST give-up's reason: a server
// answering 503 is not one that "could not be reached". Files already
// running finish their own budget. A cancel marks every file not yet
// started as cancelled, without reading it.
//
// A give-up stops the import only if NO lane has made progress for a whole
// budget. One flaky file (changed or moved during the import, or one the
// server always fails) exhausts its own budget while the other lanes keep
// landing bytes; stopping then would halt a healthy import and blame the
// server for one file's fault. That file's error is recorded, and the
// queue goes on.

export async function importFiles(files, { parallel = PARALLEL, onFile = () => {}, ...opts } = {}) {
  const results = new Array(files.length);
  let next = 0;
  let stopped = null;                          // the first give-up's reason
  const now = opts.now ?? (() => Date.now());
  const budgetMs = opts.budgetMs ?? RETRY_BUDGET_MS;
  let lastProgress = now();                    // any lane's last server answer that moved a file
  async function lane() {
    while (next < files.length) {
      const i = next++;
      if (opts.signal?.aborted) {
        results[i] = { ok: false, error: cancelled() };
        onFile(i, { done: true, result: results[i] });
        continue;
      }
      if (stopped !== null) {
        results[i] = { ok: false, error: new UploadError(`not attempted: ${stopped}`) };
        onFile(i, { done: true, result: results[i] });
        continue;
      }
      try {
        const done = await uploadFile({
          ...opts, file: files[i],
          onProgress: (sent, total) => { lastProgress = now(); onFile(i, { sent, total }); },
        });
        lastProgress = now();
        results[i] = { ok: true, ...done };
      } catch (error) {
        results[i] = { ok: false, error };
        if (error instanceof UploadError && !error.fatal && stopped === null
            && now() - lastProgress >= budgetMs) {
          stopped = error.message;
        }
      }
      onFile(i, { done: true, result: results[i] });
    }
  }
  await Promise.all(Array.from({ length: Math.min(parallel, files.length) }, lane));
  return results;
}
