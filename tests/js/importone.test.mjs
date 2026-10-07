// The interim screen imports one drawing as a draft batch of one, through
// the chunked protocol (batch design §2, decisions 17 and 21). What this
// flow decides is when a draft is KEPT for resuming and when it is thrown
// away -- data-integrity on the client side:
//  * the draft is remembered (localStorage) before the first byte, so the
//    same file selected again after a failure, a reload or a closed tab
//    resumes into it;
//  * a remembered draft whose file already finished arriving is answered
//    from the server's staging view, never uploaded a second time (a new
//    upload would stage the same drawing twice in one draft);
//  * a remembered draft that is gone or already started falls back ONCE to
//    a fresh draft;
//  * only a FATAL failure discards the draft; "not now" keeps it.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { importSingle, stagesOnly } = await import('../../app/static/js/importone.js');
const { UploadError, recallDraft, rememberDraft } = await import('../../app/static/js/upload.js');

function memoryStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)); },
    removeItem: (k) => { m.delete(k); },
    size: () => m.size,
  };
}

const FILE = { name: 'part.pdf', size: 1234, lastModified: 1700000000000 };
const FP = 'f'.repeat(64);
const complete = (id, over = {}) => ({ id, name: 'part.pdf', rel_path: '', size: 1234, state: 'complete',
                                       fingerprint: FP, ...over });
const row = (over = {}) => ({ id: 'd'.repeat(32), batch_id: 'b1', upload_id: 'u1', file_name: 'part.pdf',
                              pages: 1, unreadable: false, included: true, ...over });

// A fake server side: every dependency records its calls.
function deps(over = {}) {
  const calls = { create: 0, getBatch: [], upload: [], discard: [] };
  let n = 0;
  const d = {
    calls,
    createBatch: async () => { calls.create += 1; n += 1; return { id: `new${n}` }; },
    getBatch: async (id) => { calls.getBatch.push(id); throw Object.assign(new Error('Import not found'), { status: 404 }); },
    uploadFile: async ({ batchId }) => { calls.upload.push(batchId); return { state: 'complete', documents: [row({ batch_id: batchId })] }; },
    discardBatch: async (id) => { calls.discard.push(id); return { ok: true, status: 200 }; },
    fingerprint: async () => FP,
    ...over,
  };
  return d;
}

test('a new file goes into a fresh draft, remembered BEFORE the first byte', async () => {
  const storage = memoryStorage();
  const d = deps({
    uploadFile: async ({ batchId }) => {
      // Remembered already while the bytes are on their way: a tab closed
      // mid-upload must find this draft again.
      assert.equal(recallDraft(storage, FILE), batchId);
      d.calls.upload.push(batchId);
      return { state: 'complete', documents: [row({ batch_id: batchId })] };
    },
  });
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.equal(out.batchId, 'new1');
  assert.equal(out.row.batch_id, 'new1');
  assert.deepEqual(d.calls.upload, ['new1']);
  assert.deepEqual(d.calls.getBatch, []);
  // Still remembered after success: a tab closed at the confirm screen
  // finds the staged drawing again instead of importing it twice.
  assert.equal(recallDraft(storage, FILE), 'new1');
});

test('a remembered draft still receiving is resumed in place, not re-created', async () => {
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [],
                               uploads: [{ id: 'u9', name: 'part.pdf', rel_path: '', size: 1234, state: 'receiving' }] }),
  });
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.equal(out.batchId, 'old');
  assert.equal(d.calls.create, 0);
  assert.deepEqual(d.calls.upload, ['old']);
});

test('a remembered draft whose upload already completed is answered from the server, not uploaded again', async () => {
  // The completion's reply was lost (or the tab closed right after it):
  // the server re-creates an upload for a COMPLETE one, so uploading again
  // would stage the same drawing twice in one draft.
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const staged = row({ batch_id: 'old', upload_id: 'u9' });
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [staged],
                               uploads: [{ id: 'u9', name: 'part.pdf', rel_path: '', size: 1234, state: 'complete', fingerprint: FP }] }),
  });
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.deepEqual(out, { batchId: 'old', row: staged });
  assert.deepEqual(d.calls.upload, []);
  assert.equal(d.calls.create, 0);
});

test('a completed upload of ANOTHER file in the draft is not taken for this one', async () => {
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [row({ upload_id: 'u9', size: 99 })],
                               uploads: [{ id: 'u9', name: 'part.pdf', rel_path: '', size: 99, state: 'complete', fingerprint: FP }] }),
  });
  await importSingle({ file: FILE, storage, ...d });
  assert.deepEqual(d.calls.upload, ['old']);
});

test('a completed upload whose drawing was removed since is uploaded again, not called unreadable', async () => {
  // Stop on the interim screen deletes the staged drawing but leaves its
  // completed upload row: an empty completion is not a damaged file.
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [],
                               uploads: [{ id: 'u9', name: 'part.pdf', rel_path: '', size: 1234, state: 'complete', fingerprint: FP }] }),
  });
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.equal(out.batchId, 'old');
  assert.equal(out.unreadable, undefined);
  assert.deepEqual(d.calls.upload, ['old']);
  assert.deepEqual(d.calls.discard, []);
});

test('EVERY completed upload of this file is considered: the staged one is found even behind an emptied one', async () => {
  // Stop deleted the first upload's drawing; the file was selected again and
  // completed a second upload; then the tab closed. Taking only the FIRST
  // matching upload would find no row and upload the file a third time.
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const staged = row({ batch_id: 'old', upload_id: 'u2' });
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [staged],
                               uploads: [complete('u1'), complete('u2')] }),
  });
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.deepEqual(out, { batchId: 'old', row: staged });
  assert.deepEqual(d.calls.upload, []);
});

test('with several staged copies, the last one is shown', async () => {
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const first = row({ id: '1'.repeat(32), upload_id: 'u1' });
  const last = row({ id: '2'.repeat(32), upload_id: 'u2' });
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [first, last],
                               uploads: [complete('u1'), complete('u2')] }),
  });
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.equal(out.row, last);
});

test('a completed upload with the same name, size and time but OTHER content is not this file', async () => {
  // `cp -p` / `rsync -t` keep the modification time across different
  // content: the fingerprint tells them apart.
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [row({ upload_id: 'u9' })],
                               uploads: [complete('u9', { fingerprint: 'e'.repeat(64) })] }),
  });
  await importSingle({ file: FILE, storage, ...d });
  assert.deepEqual(d.calls.upload, ['old']);
});

test('a file that cannot be read keeps a REMEMBERED draft: its partial bytes are still good', async () => {
  const unreadable = () => Object.assign(new UploadError('the file could not be read', { fatal: true }),
                                         { reason: 'file_unreadable' });
  // Read while checking the draft for a completed upload ...
  {
    const storage = memoryStorage();
    rememberDraft(storage, FILE, 'old');
    const d = deps({
      getBatch: async (id) => ({ batch: { id, draft: true }, documents: [], uploads: [complete('u9')] }),
      fingerprint: async () => { throw unreadable(); },
    });
    await assert.rejects(importSingle({ file: FILE, storage, ...d }), (e) => e.reason === 'file_unreadable');
    assert.deepEqual(d.calls.discard, []);
    assert.equal(recallDraft(storage, FILE), 'old');
  }
  // ... or by the upload itself.
  {
    const storage = memoryStorage();
    rememberDraft(storage, FILE, 'old');
    const d = deps({
      getBatch: async (id) => ({ batch: { id, draft: true }, documents: [], uploads: [] }),
      uploadFile: async () => { throw unreadable(); },
    });
    await assert.rejects(importSingle({ file: FILE, storage, ...d }));
    assert.deepEqual(d.calls.discard, []);
    assert.equal(recallDraft(storage, FILE), 'old');
  }
  // A FRESH draft holds nothing of the file yet: it may go.
  {
    const storage = memoryStorage();
    const d = deps({ uploadFile: async () => { throw unreadable(); } });
    await assert.rejects(importSingle({ file: FILE, storage, ...d }));
    assert.deepEqual(d.calls.discard, ['new1']);
    assert.equal(recallDraft(storage, FILE), null);
  }
});

test('Start is offered only for a draft that holds exactly the drawing on screen', () => {
  const id = 'd'.repeat(32);
  const view = (docs, draft = true) => ({ batch: { draft }, documents: docs });
  assert.equal(stagesOnly(view([{ id }]), id), true);
  // A second copy (an earlier attempt, or another tab) would be queued too.
  assert.equal(stagesOnly(view([{ id }, { id: 'e'.repeat(32) }]), id), false);
  assert.equal(stagesOnly(view([{ id: 'e'.repeat(32) }]), id), false);
  assert.equal(stagesOnly(view([]), id), false);
  assert.equal(stagesOnly(view([{ id }], false), id), false);
  assert.equal(stagesOnly(null, id), false);
  assert.equal(stagesOnly({}, id), false);
});

test('a remembered draft that is gone (404) falls back to a fresh draft', async () => {
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'gone');
  const d = deps();
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.equal(out.batchId, 'new1');
  assert.deepEqual(d.calls.upload, ['new1']);
  assert.equal(recallDraft(storage, FILE), 'new1');
  assert.deepEqual(d.calls.discard, []);
});

test('a remembered draft that has already started falls back to a fresh draft', async () => {
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'started');
  const d = deps({ getBatch: async (id) => ({ batch: { id, draft: false }, documents: [], uploads: [] }) });
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.equal(out.batchId, 'new1');
  assert.deepEqual(d.calls.upload, ['new1']);
  // A started import is never discarded on its way out.
  assert.deepEqual(d.calls.discard, []);
});

test('a remembered draft refused by the upload itself (404/409) falls back ONCE', async () => {
  for (const status of [404, 409]) {
    const storage = memoryStorage();
    rememberDraft(storage, FILE, 'old');
    const d = deps({
      getBatch: async (id) => ({ batch: { id, draft: true }, documents: [], uploads: [] }),
      uploadFile: async ({ batchId }) => {
        d.calls.upload.push(batchId);
        if (batchId === 'old') throw new UploadError('refused', { status, fatal: true });
        return { state: 'complete', documents: [row({ batch_id: batchId })] };
      },
    });
    const out = await importSingle({ file: FILE, storage, ...d });
    assert.equal(out.batchId, 'new1');
    assert.deepEqual(d.calls.upload, ['old', 'new1']);
    assert.deepEqual(d.calls.discard, []);
  }
});

test('the fallback happens once: a fresh draft refused the same way is a failure, and is discarded', async () => {
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [], uploads: [] }),
    uploadFile: async ({ batchId }) => {
      d.calls.upload.push(batchId);
      throw new UploadError('refused', { status: 409, fatal: true });
    },
  });
  await assert.rejects(importSingle({ file: FILE, storage, ...d }), (e) => e.status === 409);
  assert.deepEqual(d.calls.upload, ['old', 'new1']);
  assert.equal(d.calls.create, 1);
  assert.deepEqual(d.calls.discard, ['new1']);
  assert.equal(recallDraft(storage, FILE), null);
});

test('a non-fatal failure keeps the draft and its memory, so selecting the file again resumes', async () => {
  const storage = memoryStorage();
  const d = deps({
    uploadFile: async ({ batchId }) => {
      d.calls.upload.push(batchId);
      throw new UploadError('the server could not be reached', { status: 0 });
    },
  });
  await assert.rejects(importSingle({ file: FILE, storage, ...d }), (e) => e instanceof UploadError && !e.fatal);
  assert.deepEqual(d.calls.discard, []);
  assert.equal(recallDraft(storage, FILE), 'new1');
});

test('a fatal failure discards the draft and forgets it', async () => {
  const storage = memoryStorage();
  const d = deps({
    uploadFile: async () => { throw new UploadError('not enough free disk space on the server', { status: 507, fatal: true }); },
  });
  await assert.rejects(importSingle({ file: FILE, storage, ...d }), (e) => e.status === 507);
  assert.deepEqual(d.calls.discard, ['new1']);
  assert.equal(recallDraft(storage, FILE), null);
});

test('a non-fatal failure on a REMEMBERED draft keeps it too', async () => {
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  const d = deps({
    getBatch: async (id) => ({ batch: { id, draft: true }, documents: [], uploads: [] }),
    uploadFile: async () => { throw new UploadError('server error (503)', { status: 503 }); },
  });
  await assert.rejects(importSingle({ file: FILE, storage, ...d }));
  assert.deepEqual(d.calls.discard, []);
  assert.equal(recallDraft(storage, FILE), 'old');
});

test('a staging view that cannot be fetched (not a 404) keeps the draft: it is not known to be gone', async () => {
  const storage = memoryStorage();
  rememberDraft(storage, FILE, 'old');
  for (const err of [new TypeError('Failed to fetch'), Object.assign(new Error('x'), { status: 502 })]) {
    const d = deps({ getBatch: async () => { throw err; } });
    await assert.rejects(importSingle({ file: FILE, storage, ...d }), (e) => e instanceof UploadError && !e.fatal);
    assert.equal(d.calls.create, 0);
    assert.deepEqual(d.calls.upload, []);
    assert.deepEqual(d.calls.discard, []);
    assert.equal(recallDraft(storage, FILE), 'old');
  }
});

test('a failure before any draft exists discards nothing', async () => {
  const storage = memoryStorage();
  const d = deps({ createBatch: async () => { throw new TypeError('Failed to fetch'); } });
  await assert.rejects(importSingle({ file: FILE, storage, ...d }));
  assert.deepEqual(d.calls.discard, []);
  assert.equal(storage.size(), 0);
});

test('an unreadable PDF discards the draft and forgets it', async () => {
  const storage = memoryStorage();
  const d = deps({
    uploadFile: async ({ batchId }) => ({ state: 'complete', documents: [row({ batch_id: batchId, unreadable: true })] }),
  });
  const out = await importSingle({ file: FILE, storage, ...d });
  assert.equal(out.unreadable, true);
  assert.deepEqual(d.calls.discard, ['new1']);
  assert.equal(recallDraft(storage, FILE), null);
});

test('storage that throws on every access still imports (resume is a convenience)', async () => {
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); },
                   removeItem() { throw new Error('denied'); } };
  const d = deps();
  const out = await importSingle({ file: FILE, storage: broken, ...d });
  assert.equal(out.batchId, 'new1');
  const out2 = await importSingle({ file: FILE, storage: null, ...deps() });
  assert.equal(out2.batchId, 'new1');
});

test('progress reaches the caller', async () => {
  const seen = [];
  const d = deps({
    uploadFile: async ({ batchId, onProgress }) => {
      onProgress(5, 10);
      return { state: 'complete', documents: [row({ batch_id: batchId })] };
    },
  });
  await importSingle({ file: FILE, storage: memoryStorage(), ...d, onProgress: (s, t) => seen.push([s, t]) });
  assert.deepEqual(seen, [[5, 10]]);
});
