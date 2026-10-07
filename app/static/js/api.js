// Thin fetch wrappers — single place for endpoint URLs.

// Save a PDF (no extraction). Resolves to { session_id, fileName, pages }.
export async function savePdf(file) {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/api/upload', { method: 'POST', body: fd });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Upload failed' }));
    throw new Error(err.detail || 'Upload failed');
  }
  return res.json();
}

export async function readRegion(sessionId, box) {
  const res = await fetch('/api/read_region', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId, box }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Region read failed' }));
    throw new Error(err.detail || 'Region read failed');
  }
  return res.json();
}

export async function health() {
  try {
    const res = await fetch('/api/health');
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export async function postEvents(sessionId, writer, events) {
  const res = await fetch(`/api/session/${sessionId}/events`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ writer, events }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Could not save the review log' }));
    throw new Error(err.detail || 'Could not save the review log');
  }
  return res.json();
}

// Last-chance flush when the page goes away; the server dedups by seq, so a
// beacon that races a normal flush costs nothing.
export function beaconEvents(sessionId, writer, events) {
  if (!navigator.sendBeacon || !events.length) return;
  const blob = new Blob([JSON.stringify({ writer, events })], { type: 'application/json' });
  navigator.sendBeacon(`/api/session/${sessionId}/events`, blob);
}

export async function sealSession(sessionId, body) {
  const res = await fetch(`/api/session/${sessionId}/seal`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Could not finish' }));
    throw new Error(err.detail || 'Could not finish');
  }
  return res.json();
}

// A FastAPI validation error (422) carries `detail` as an array of error
// objects, not a string -- `err.detail || fallback` happily stringified one
// of those to "[object Object]" instead of falling back. Only a STRING
// detail is ever shown; any other shape (array, object, or no body at all)
// falls back, and a body that parsed but holds a non-string detail says so
// with the status code rather than silently reusing the caller's fallback
// (which is usually written for a 404, not a validation error).
//
// The thrown error carries `status`, so a caller can tell "gone" (404) from
// a hiccup -- a #doc link is dropped only for the former.
export async function jsonOrThrow(res, fallback) {
  if (!res.ok) {
    let body = null;
    try { body = await res.json(); } catch { /* no JSON body at all */ }
    let msg = fallback;
    if (body && typeof body.detail === 'string') msg = body.detail;
    else if (body && body.detail !== undefined) msg = `request rejected (${res.status})`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

const docUrl = (id, tail = '') => `/api/documents/${id}${tail}`;

export const getDocument = async (id) => jsonOrThrow(await fetch(docUrl(id)), 'Drawing not found');
export const enqueueDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/enqueue'), { method: 'POST' }), 'Could not queue the drawing');
export const cancelDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/cancel'), { method: 'POST' }), 'Could not cancel');
export const retryDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/retry'), { method: 'POST' }), 'Could not retry');
export const openDocument = async (id) =>
  jsonOrThrow(await fetch(docUrl(id, '/open'), { method: 'POST' }), 'Could not open the drawing');

// ----- Imports (batch design §2). The chunked upload itself is upload.js.
// The server cleans a batch name and may refuse it (400, a fixed sentence);
// errors carry `status` via jsonOrThrow.
const JSON_HEADERS = { 'Content-Type': 'application/json' };
// An id is a path SEGMENT: encoded, so a stray '/' or '?' (a remembered
// draft id from storage is not trusted) can never address another route.
const batchUrl = (id, tail = '') => `/api/batches/${encodeURIComponent(id)}${tail}`;

export const createBatch = async (name) =>
  jsonOrThrow(await fetch('/api/batches', { method: 'POST', headers: JSON_HEADERS,
                                            body: JSON.stringify({ name }) }), 'Could not start an import');
export const getBatch = async (id) => jsonOrThrow(await fetch(batchUrl(id)), 'Import not found');
export const startBatch = async (id) =>
  jsonOrThrow(await fetch(batchUrl(id, '/start'), { method: 'POST' }), 'Could not start the import');
export const setIncluded = async (id, included) =>
  jsonOrThrow(await fetch(docUrl(id, '/include'), { method: 'POST', headers: JSON_HEADERS,
                                                    body: JSON.stringify({ included }) }),
              'Could not change the selection');

// Best effort, like deleteDocument: never throws; resolves { ok, status }.
export async function discardBatch(id) {
  if (!id) return { ok: false, status: 0 };
  try {
    const res = await fetch(batchUrl(id), { method: 'DELETE' });
    return { ok: res.ok, status: res.status };
  } catch {
    return { ok: false, status: 0 };
  }
}

// Best-effort removal of a drawing the user backed out of. Never throws;
// resolves { ok, status } (status 0 = no response). The server refuses
// (409) while a cancelled run is still unwinding, which Stop waits out; any
// other failure is a real fault it must not spin on forever (stop.js).
export async function deleteDocument(id) {
  if (!id) return { ok: false, status: 0 };
  try {
    const res = await fetch(docUrl(id), { method: 'DELETE' });
    return { ok: res.ok, status: res.status };
  } catch {
    return { ok: false, status: 0 };
  }
}

// The server always sends both forms (app/main.py _content_disposition):
// `filename="<ascii-sanitised>"; filename*=UTF-8''<percent-encoded>`. Prefer
// the UTF-8 form so a non-ASCII name (umlauts, etc.) downloads under its real
// name instead of the "_"-mangled ASCII fallback. The old single regex
// `/filename="?([^"]+)"?/` made the quotes optional, so on an UNQUOTED match
// `[^"]+` ran to the end of the header and swallowed the following
// `; filename*=...` parameter whole -- the quoted branch below requires the
// closing quote for exactly that reason.
export function contentDispositionFilename(header, fallback) {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(header || '');
  if (star) {
    try { return decodeURIComponent(star[1]); } catch { /* malformed percent-encoding: fall through */ }
  }
  const quoted = /filename="([^"]+)"/.exec(header || '');
  return quoted ? quoted[1] : fallback;
}

// Download a sealed revision's file; the server names it after the drawing.
export async function downloadExport(id, kind, revision) {
  const res = await fetch(docUrl(id, `/export/${kind}?rev=${revision}`));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Export failed' }));
    throw new Error(err.detail || 'Export failed');
  }
  const name = contentDispositionFilename(res.headers.get('content-disposition'), `drawing.${kind}`);
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
