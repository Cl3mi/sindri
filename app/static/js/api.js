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

// Run extraction for a saved session and stream progress.
// `onProgress({ step, detail, current, total })` is called per step; the
// resolved value is the final extraction result. Pass an AbortController
// `signal` to stop mid-run — an aborted fetch rejects with an AbortError.
export async function runExtraction(sessionId, onProgress, signal) {
  const res = await fetch(`/api/extract/${sessionId}`, { method: 'POST', signal });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Extraction failed' }));
    throw new Error(err.detail || 'Extraction failed');
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let result = null;
  let errDetail = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });

    let sep;
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const block = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const ev = parseEvent(block);
      if (!ev) continue;
      if (ev.event === 'progress') onProgress && onProgress(ev.data);
      else if (ev.event === 'result') result = ev.data;
      else if (ev.event === 'error') errDetail = ev.data && ev.data.detail;
    }
  }

  if (errDetail) throw new Error(errDetail);
  if (!result) throw new Error('Extraction ended without a result');
  return result;
}

// Discard a saved session (fire-and-forget).
export async function deleteSession(sessionId) {
  if (!sessionId) return;
  try {
    await fetch(`/api/session/${sessionId}`, { method: 'DELETE' });
  } catch {
    /* best-effort cleanup */
  }
}

function parseEvent(block) {
  let event = 'message';
  const dataLines = [];
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) };
  } catch {
    return null;
  }
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

export async function exportFile(endpoint, payload, filename) {
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Export failed' }));
    throw new Error(err.detail || 'Export failed');
  }
  const blob = await res.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
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
