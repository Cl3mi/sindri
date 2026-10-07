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
