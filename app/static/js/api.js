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

async function jsonOrThrow(res, fallback) {
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: fallback }));
    throw new Error(err.detail || fallback);
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

// Fire-and-forget removal of a staged drawing the user backed out of.
export async function deleteDocument(id) {
  if (!id) return;
  try { await fetch(docUrl(id), { method: 'DELETE' }); } catch { /* best effort */ }
}

// Download a sealed revision's file; the server names it after the drawing.
export async function downloadExport(id, kind, revision) {
  const res = await fetch(docUrl(id, `/export/${kind}?rev=${revision}`));
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Export failed' }));
    throw new Error(err.detail || 'Export failed');
  }
  const name = /filename="?([^"]+)"?/.exec(res.headers.get('content-disposition') || '')?.[1]
    || `drawing.${kind}`;
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}
