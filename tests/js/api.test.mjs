// api.js helpers that don't touch the DOM or the network directly, so node
// can exercise them without a fetch/document shim. The server's content-
// disposition shape is app/main.py's `_content_disposition`: a quoted ASCII
// fallback plus a `filename*=UTF-8''<percent-encoded>` form, always both.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { contentDispositionFilename, jsonOrThrow } = await import('../../app/static/js/api.js');

test('prefers the UTF-8 filename* form so a non-ASCII name downloads correctly', () => {
  // _content_disposition('Gehäuse_Größe.xlsx'): non-ASCII chars become "_"
  // in the quoted fallback, and the real name only survives in filename*.
  const header = 'attachment; filename="Geh_use_Gr__e.xlsx"; '
    + "filename*=UTF-8''Geh%C3%A4use_Gr%C3%B6%C3%9Fe.xlsx";
  assert.equal(contentDispositionFilename(header, 'drawing.xlsx'), 'Gehäuse_Größe.xlsx');
});

test('falls back to the quoted name when there is no filename* at all', () => {
  const header = 'attachment; filename="drawing.xlsx"';
  assert.equal(contentDispositionFilename(header, 'fallback.xlsx'), 'drawing.xlsx');
});

test('a malformed filename* falls back to the quoted name, not a mangled decode', () => {
  const header = "attachment; filename=\"clean.xlsx\"; filename*=UTF-8''%";
  assert.equal(contentDispositionFilename(header, 'fallback.xlsx'), 'clean.xlsx');
});

test('no content-disposition header at all uses the caller fallback', () => {
  assert.equal(contentDispositionFilename(null, 'drawing.xlsx'), 'drawing.xlsx');
});

function fakeRes(status, body) {
  return { ok: status < 400, status, json: async () => body };
}

test('jsonOrThrow uses a string detail as the message', async () => {
  await assert.rejects(jsonOrThrow(fakeRes(404, { detail: 'Drawing not found' }), 'fallback'),
    /Drawing not found/);
});

test('jsonOrThrow does not stringify a FastAPI 422 array detail to [object Object]', async () => {
  const body = { detail: [{ loc: ['body', 'x'], msg: 'field required', type: 'missing' }] };
  await assert.rejects(jsonOrThrow(fakeRes(422, body), 'fallback'), (err) => {
    assert.equal(err.message, 'request rejected (422)');
    assert.ok(!err.message.includes('[object Object]'));
    return true;
  });
});

test('jsonOrThrow falls back to the caller message when the body is not JSON at all', async () => {
  const res = { ok: false, status: 500, json: async () => { throw new Error('not json'); } };
  await assert.rejects(jsonOrThrow(res, 'Could not queue the drawing'), /Could not queue the drawing/);
});

test('jsonOrThrow resolves the parsed body on a 2xx response', async () => {
  assert.deepEqual(await jsonOrThrow(fakeRes(200, { ok: true }), 'fallback'), { ok: true });
});

// Stop removes a drawing only once the worker has let go of it: DELETE is
// refused (409) while a cancelled run is still unwinding, which can take
// minutes. The caller waits out a 409 but must tell it apart from a server
// fault or a dropped network, so the status comes back (0 = no response).
test('deleteDocument reports whether the drawing was removed, and why not', async () => {
  const { deleteDocument } = await import('../../app/static/js/api.js');
  const saved = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: false, status: 409 });
    assert.deepEqual(await deleteDocument('a'.repeat(32)), { ok: false, status: 409 });
    globalThis.fetch = async () => ({ ok: true, status: 200 });
    assert.deepEqual(await deleteDocument('a'.repeat(32)), { ok: true, status: 200 });
    globalThis.fetch = async () => { throw new TypeError('offline'); };
    assert.deepEqual(await deleteDocument('a'.repeat(32)), { ok: false, status: 0 });
  } finally {
    globalThis.fetch = saved;
  }
});

// A #doc link is dropped only when the drawing is really gone (404), not
// when the server hiccups, so the error carries the status.
test('jsonOrThrow errors carry the HTTP status', async () => {
  await assert.rejects(jsonOrThrow(fakeRes(404, { detail: 'unknown document' }), 'x'),
    (err) => err.status === 404);
  const res = { ok: false, status: 503, json: async () => { throw new Error('not json'); } };
  await assert.rejects(jsonOrThrow(res, 'x'), (err) => err.status === 503);
});

// The import wrappers (batch design §2) speak the routes app/main.py has:
// the URL, method and body each sends, and a refusal carries its status.
test('the import wrappers send what the import routes expect', async () => {
  const api = await import('../../app/static/js/api.js');
  const saved = globalThis.fetch;
  const sent = [];
  try {
    globalThis.fetch = async (url, init = {}) => {
      sent.push([url, init.method || 'GET', init.body ? JSON.parse(init.body) : null]);
      return fakeRes(200, { ok: true });
    };
    await api.createBatch('Lieferung 12');
    await api.getBatch('b1');
    await api.startBatch('b1');
    await api.setIncluded('d1', false);
    assert.deepEqual(sent, [
      ['/api/batches', 'POST', { name: 'Lieferung 12' }],
      ['/api/batches/b1', 'GET', null],
      ['/api/batches/b1/start', 'POST', null],
      ['/api/documents/d1/include', 'POST', { included: false }],
    ]);
    sent.length = 0;
    await api.getBatch('a/b');
    await api.startBatch('a?b');
    assert.deepEqual(sent.map((s) => s[0]), ['/api/batches/a%2Fb', '/api/batches/a%3Fb/start']);
    sent.length = 0;
    await api.getDocument('d/1');
    await api.setIncluded('d?1', true);
    assert.deepEqual(sent.map((s) => s[0]), ['/api/documents/d%2F1', '/api/documents/d%3F1/include']);
    globalThis.fetch = async () => fakeRes(409, { detail: 'the import changed' });
    await assert.rejects(api.startBatch('b1'),
      (err) => err.status === 409 && err.message === 'the import changed');
  } finally {
    globalThis.fetch = saved;
  }
});

// The interim UI starts a draft only if it holds exactly the drawing on
// screen. Sending those ids makes the SERVER refuse (409) a draft that
// changed between the UI's look and its Start, instead of queueing a row
// the user never saw.
test('startBatch sends the ids the caller expects the draft to hold', async () => {
  const api = await import('../../app/static/js/api.js');
  const saved = globalThis.fetch;
  const sent = [];
  try {
    globalThis.fetch = async (url, init = {}) => {
      sent.push([url, init.method, init.headers?.['Content-Type'] ?? null,
                 init.body ? JSON.parse(init.body) : null]);
      return fakeRes(200, { queued: 1 });
    };
    await api.startBatch('b1', ['d1']);
    await api.startBatch('b1', []);
    await api.startBatch('b1');
    assert.deepEqual(sent, [
      ['/api/batches/b1/start', 'POST', 'application/json', { expected_ids: ['d1'] }],
      ['/api/batches/b1/start', 'POST', 'application/json', { expected_ids: [] }],
      ['/api/batches/b1/start', 'POST', null, null],
    ]);
  } finally {
    globalThis.fetch = saved;
  }
});

const { listDocuments, moveToFront, reextractDocument, pauseQueue, resumeQueue } =
  await import('../../app/static/js/api.js');

test('listDocuments builds the query string from only the filters given', async () => {
  const calls = [];
  globalThis.fetch = async (url) => { calls.push(url); return fakeRes(200, { documents: [], cursor: 'x', queue: {} }); };
  await listDocuments({ batchId: 'b1', needsMe: true });
  assert.equal(calls[0], '/api/documents?batch_id=b1&needs_me=true');
  await listDocuments({});
  assert.equal(calls[1], '/api/documents');
});

test('moveToFront, reextractDocument, pauseQueue, resumeQueue post to the right routes', async () => {
  const calls = [];
  globalThis.fetch = async (url, opts) => { calls.push([url, opts?.method]); return fakeRes(200, {}); };
  await moveToFront('a'.repeat(32));
  await reextractDocument('a'.repeat(32));
  await pauseQueue();
  await resumeQueue();
  assert.deepEqual(calls, [
    [`/api/documents/${'a'.repeat(32)}/move_to_front`, 'POST'],
    [`/api/documents/${'a'.repeat(32)}/reextract`, 'POST'],
    ['/api/queue/pause', 'POST'],
    ['/api/queue/resume', 'POST'],
  ]);
});

test('discardBatch is best effort: it reports, never throws', async () => {
  const { discardBatch } = await import('../../app/static/js/api.js');
  const saved = globalThis.fetch;
  try {
    const urls = [];
    globalThis.fetch = async (url, init) => { urls.push([url, init.method]); return { ok: true, status: 200 }; };
    assert.deepEqual(await discardBatch('b1'), { ok: true, status: 200 });
    assert.deepEqual(urls, [['/api/batches/b1', 'DELETE']]);
    globalThis.fetch = async () => ({ ok: false, status: 409 });
    assert.deepEqual(await discardBatch('b1'), { ok: false, status: 409 });
    globalThis.fetch = async () => { throw new TypeError('offline'); };
    assert.deepEqual(await discardBatch('b1'), { ok: false, status: 0 });
    assert.deepEqual(await discardBatch(''), { ok: false, status: 0 });
  } finally {
    globalThis.fetch = saved;
  }
});

test('deleteUpload is best effort: it reports, never throws', async () => {
  const { deleteUpload } = await import('../../app/static/js/api.js');
  const saved = globalThis.fetch;
  try {
    const urls = [];
    globalThis.fetch = async (url, init) => { urls.push([url, init.method]); return { ok: true, status: 200 }; };
    assert.deepEqual(await deleteUpload('u1'), { ok: true, status: 200 });
    assert.deepEqual(urls, [['/api/uploads/u1', 'DELETE']]);
    globalThis.fetch = async () => ({ ok: false, status: 507 });
    assert.deepEqual(await deleteUpload('u1'), { ok: false, status: 507 });
    globalThis.fetch = async () => { throw new TypeError('offline'); };
    assert.deepEqual(await deleteUpload('u1'), { ok: false, status: 0 });
    assert.deepEqual(await deleteUpload(''), { ok: false, status: 0 });
  } finally {
    globalThis.fetch = saved;
  }
});
