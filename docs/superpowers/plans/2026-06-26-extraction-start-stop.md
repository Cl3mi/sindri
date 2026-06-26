# Extraction Start/Stop Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Insert a confirm → Start → (live SSE) → Stop gate between selecting a PDF and extracting it, with true server-side cancellation.

**Architecture:** Split the current `/api/upload` (which saves + extracts in one shot) into `POST /api/upload` (save the PDF, return `{session_id, fileName, pages}`) and `POST /api/extract/{session_id}` (the existing SSE worker stream, now cancellable via a `threading.Event` checked in the progress callback and tripped on client disconnect). Add `DELETE /api/session/{session_id}` for cleanup. The frontend gains a `#plan-confirm` overlay and a Stop button, and drives the new endpoints with an `AbortController`.

**Tech Stack:** FastAPI + Starlette `StreamingResponse`, PyMuPDF (`fitz`), pytest + `TestClient`, vanilla ES-module frontend (`app/static/js`).

---

## File Structure

- `app/main.py` — Modify: split `upload`, add `extract_endpoint`, `delete_session`, `_Cancelled`. (Backend endpoints.)
- `tests/test_api.py` — Modify: update the `upload_pdf` helper + existing SSE tests to the new two-step flow; add metadata / extract / delete / cancellation tests.
- `app/static/js/api.js` — Modify: replace `uploadPdf` with `savePdf` + `runExtraction`; add `deleteSession`.
- `app/static/index.html` — Modify: add `#plan-confirm` overlay; add Stop button to `#plan-extracting` header.
- `app/static/js/main.js` — Modify: confirm/start/stop state machine and button wiring.
- `app/static/styles/components.css` — Modify: confirm-card + stop-button styling.

Backend is covered by pytest (TDD). The frontend (`api.js`, `index.html`, `main.js`, `components.css`) has no automated test harness in this repo, so those tasks use explicit manual-verification steps in a browser.

---

## Task 1: Split `/api/upload` (save + metadata) and add `/api/extract/{session_id}` (SSE)

**Files:**
- Modify: `app/main.py:62-109` (the current `upload` function)
- Modify: `tests/test_api.py` (the `upload_pdf` helper + two SSE tests)

- [ ] **Step 1: Update the test helper and existing SSE tests to the new two-step flow**

In `tests/test_api.py`, replace the `upload_pdf` helper (lines 35-44) with one that saves, then extracts:

```python
def save_pdf(test_client, path, filename="sample.pdf"):
    """POST a PDF to /api/upload (save only) and return its metadata."""
    with open(path, "rb") as f:
        r = test_client.post("/api/upload",
                             files={"file": (filename, f, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def upload_pdf(test_client, path, filename="sample.pdf"):
    """Save a PDF then run extraction; return the final extraction result."""
    meta = save_pdf(test_client, path, filename)
    r = test_client.post(f"/api/extract/{meta['session_id']}")
    assert r.status_code == 200, r.text
    _progress, result, error = parse_sse(r)
    assert error is None, error
    assert result is not None
    return result
```

Replace `test_upload_streams_progress_events` (lines 66-78) with an extract-endpoint version:

```python
def test_extract_streams_progress_events(sample_pdf, stub_backend):
    meta = save_pdf(client, sample_pdf)
    r = client.post(f"/api/extract/{meta['session_id']}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    progress, result, error = parse_sse(r)
    assert error is None
    assert result is not None
    steps = [p["step"] for p in progress]
    assert steps[0] == "render"
    assert "detect" in steps and "ocr" in steps and "place" in steps
```

Replace `test_upload_without_detection_backend_streams_error` (lines 81-93):

```python
def test_extract_without_detection_backend_streams_error(sample_pdf, monkeypatch):
    class ReadOnly:
        def read_region(self, image):
            from app.pipeline.ocr.base import OcrResult
            return OcrResult(text="", confidence=0.0)
    monkeypatch.setattr("app.main._BACKEND", ReadOnly())
    meta = save_pdf(client, sample_pdf)
    r = client.post(f"/api/extract/{meta['session_id']}")
    assert r.status_code == 200
    _progress, result, error = parse_sse(r)
    assert result is None
    assert error is not None and "VLM" in error["detail"]
```

Add a new test asserting `/api/upload` no longer extracts:

```python
def test_upload_returns_metadata_without_extracting(sample_pdf, stub_backend):
    import app.main as main
    meta = save_pdf(client, sample_pdf)
    assert main._SESSION_RE.match(meta["session_id"])
    assert meta["fileName"] == "sample.pdf"
    assert meta["pages"] >= 1
    # save-only: the input pdf is stored but no extraction artifacts exist yet
    work = main._SESSIONS / meta["session_id"]
    assert (work / "input.pdf").is_file()
    assert not (work / "page.png").exists()


def test_extract_rejects_bad_session_id():
    # malformed id is rejected by _session_dir before any work
    assert client.post("/api/extract/not-a-valid-uuid").status_code == 404


def test_extract_unknown_session_returns_404():
    # well-formed id but no saved input.pdf
    assert client.post("/api/extract/" + "0" * 32).status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -k "extract or metadata or notes or roundtrip or read_region" -v`
Expected: FAIL — `/api/extract/...` returns 404/405 (endpoint doesn't exist) and `/api/upload` returns an SSE stream instead of JSON metadata.

- [ ] **Step 3: Implement the split in `app/main.py`**

Add `Request` to the FastAPI import (line 10) and `import fitz` near the other imports (after line 8):

```python
from fastapi import FastAPI, UploadFile, File, HTTPException, Request
```

```python
import fitz  # PyMuPDF — used for a cheap page-count on upload
```

Replace the entire `upload` function (lines 62-109) with the two endpoints below:

```python
@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    """Save the PDF to a fresh session and return its metadata. Extraction is
    a separate, explicitly-started step (`POST /api/extract/{session_id}`)."""
    session_id = uuid.uuid4().hex
    work = _SESSIONS / session_id
    work.mkdir(parents=True, exist_ok=True)
    pdf_path = work / "input.pdf"
    pdf_path.write_bytes(await file.read())
    try:
        with fitz.open(pdf_path) as doc:
            pages = doc.page_count
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise HTTPException(status_code=400, detail="could not read the PDF")
    return {"session_id": session_id, "fileName": file.filename, "pages": pages}


@app.post("/api/extract/{session_id}")
async def extract_endpoint(session_id: str, request: Request):
    """Run extraction for an already-uploaded session and stream pipeline
    progress as Server-Sent Events. The final result is a terminal `result`
    (or `error`) event."""
    work = _session_dir(session_id)
    pdf_path = work / "input.pdf"
    if not pdf_path.is_file():
        raise HTTPException(status_code=404, detail="unknown session")

    events: "queue.Queue" = queue.Queue()
    _DONE = object()

    def worker():
        def progress(step, detail="", current=None, total=None):
            events.put(("progress", {
                "step": step, "detail": detail,
                "current": current, "total": total,
            }))
        try:
            result = extract(pdf_path, work_dir=work, dpi=300,
                             backend=_BACKEND, progress=progress)
            events.put(("result", {
                "session_id": session_id,
                "image_url": f"/api/image/{session_id}",
                "rows": [r.model_dump() for r in result.characteristics],
                "notes": result.notes.model_dump() if result.notes is not None else None,
            }))
        except RuntimeError as e:
            events.put(("error", {"detail": str(e)}))
        except Exception:
            events.put(("error", {"detail": "could not read the PDF"}))
        finally:
            events.put((_DONE, None))

    async def stream():
        threading.Thread(target=worker, daemon=True).start()
        loop = asyncio.get_event_loop()
        while True:
            kind, payload = await loop.run_in_executor(None, events.get)
            if kind is _DONE:
                break
            yield f"event: {kind}\ndata: {json.dumps(payload)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
```

Add `import shutil` to the top-of-file imports (after `import re`, line 4):

```python
import shutil
```

(Cancellation is added in Task 2 — this task only relocates the SSE worker into `extract_endpoint` and reduces `upload` to save+metadata.)

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -k "extract or metadata or notes or roundtrip or read_region" -v`
Expected: PASS for all selected tests.

- [ ] **Step 5: Run the full backend suite to confirm nothing else broke**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: PASS (no remaining references to the old single-endpoint behavior).

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: split /api/upload into save + /api/extract SSE endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Cooperative cancellation of a running extraction

**Files:**
- Modify: `app/main.py` (`extract_endpoint` — add `_Cancelled`, cancel event, disconnect poll, worker cleanup)
- Modify: `tests/test_api.py` (cancellation unit test)

- [ ] **Step 1: Write the failing cancellation test**

Add to `tests/test_api.py`. This exercises the cancel hook directly — when the `progress` callback is wired to a set event it raises `_Cancelled`, and `extract()` stops before returning a full result. No live HTTP disconnect needed.

```python
def test_extraction_cancels_when_event_set(sample_pdf, stub_backend, tmp_path):
    import threading
    import app.main as main
    from app.pipeline.extract import extract

    cancel = threading.Event()
    cancel.set()  # already cancelled before the first progress emit
    seen = []

    def progress(step, detail="", current=None, total=None):
        seen.append(step)
        if cancel.is_set():
            raise main._Cancelled()

    with pytest.raises(main._Cancelled):
        extract(sample_pdf, work_dir=tmp_path, dpi=300,
                backend=stub_backend, progress=progress)
    # it unwound at the very first emit ("render"), never reaching "place"
    assert seen == ["render"]
    assert "place" not in seen
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_api.py::test_extraction_cancels_when_event_set -v`
Expected: FAIL — `AttributeError: module 'app.main' has no attribute '_Cancelled'`.

- [ ] **Step 3: Add `_Cancelled` and wire cancellation into `extract_endpoint`**

In `app/main.py`, define the exception just above `extract_endpoint`:

```python
class _Cancelled(Exception):
    """Raised inside the extraction worker when the client disconnects, to
    unwind the pipeline cooperatively at the next progress emit."""
```

Then update `extract_endpoint` so the worker checks a `threading.Event` in its `progress` callback, cleans up on cancel, and the stream loop trips the event on client disconnect. Replace the `events`/`worker`/`stream` block inside `extract_endpoint` with:

```python
    events: "queue.Queue" = queue.Queue()
    _DONE = object()
    cancel = threading.Event()

    def worker():
        def progress(step, detail="", current=None, total=None):
            if cancel.is_set():
                raise _Cancelled()
            events.put(("progress", {
                "step": step, "detail": detail,
                "current": current, "total": total,
            }))
        try:
            result = extract(pdf_path, work_dir=work, dpi=300,
                             backend=_BACKEND, progress=progress)
            events.put(("result", {
                "session_id": session_id,
                "image_url": f"/api/image/{session_id}",
                "rows": [r.model_dump() for r in result.characteristics],
                "notes": result.notes.model_dump() if result.notes is not None else None,
            }))
        except _Cancelled:
            # client went away — drop the session and stop quietly
            shutil.rmtree(work, ignore_errors=True)
        except RuntimeError as e:
            events.put(("error", {"detail": str(e)}))
        except Exception:
            events.put(("error", {"detail": "could not read the PDF"}))
        finally:
            events.put((_DONE, None))

    async def stream():
        threading.Thread(target=worker, daemon=True).start()
        loop = asyncio.get_event_loop()
        while True:
            try:
                kind, payload = await loop.run_in_executor(
                    None, lambda: events.get(timeout=0.25))
            except queue.Empty:
                if await request.is_disconnected():
                    cancel.set()
                    break
                continue
            if kind is _DONE:
                break
            yield f"event: {kind}\ndata: {json.dumps(payload)}\n\n"
```

The worker's `except _Cancelled` handler removes the session dir only after the pipeline has stopped touching it, avoiding a race with the still-running thread. (The frontend also issues an explicit `DELETE` — see Task 3 — so cleanup is belt-and-braces.)

- [ ] **Step 4: Run the cancellation test to verify it passes**

Run: `.venv/bin/pytest tests/test_api.py::test_extraction_cancels_when_event_set -v`
Expected: PASS.

- [ ] **Step 5: Run the full api suite to confirm normal streaming still works**

Run: `.venv/bin/pytest tests/test_api.py -v`
Expected: PASS — the `timeout=0.25` poll loop must not regress normal completion (`test_extract_streams_progress_events`, `upload_pdf`-based tests).

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: cancel a running extraction on client disconnect

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `DELETE /api/session/{session_id}` cleanup endpoint

**Files:**
- Modify: `app/main.py` (add `delete_session`)
- Modify: `tests/test_api.py` (delete tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_api.py`:

```python
def test_delete_session_removes_dir(sample_pdf):
    meta = save_pdf(client, sample_pdf)
    import app.main as main
    work = main._SESSIONS / meta["session_id"]
    assert work.is_dir()
    r = client.delete(f"/api/session/{meta['session_id']}")
    assert r.status_code == 200
    assert not work.exists()


def test_delete_missing_session_is_idempotent():
    r = client.delete("/api/session/" + "0" * 32)
    assert r.status_code == 200


def test_delete_rejects_bad_session_id():
    r = client.delete("/api/session/not-a-valid-uuid")
    assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_api.py -k delete -v`
Expected: FAIL — `405 Method Not Allowed` / endpoint not found.

- [ ] **Step 3: Add the endpoint in `app/main.py`**

Add after `extract_endpoint`:

```python
@app.delete("/api/session/{session_id}")
def delete_session(session_id: str):
    """Discard an uploaded session (cancelled confirm, or stopped extraction).
    Idempotent: a missing directory is not an error; a malformed id is 404
    via `_session_dir`."""
    work = _session_dir(session_id)
    shutil.rmtree(work, ignore_errors=True)
    return {"ok": True}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_api.py -k delete -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat: add DELETE /api/session/{id} to discard an upload

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Frontend API client — `savePdf`, `runExtraction`, `deleteSession`

**Files:**
- Modify: `app/static/js/api.js:6-41` (replace `uploadPdf`)

- [ ] **Step 1: Replace `uploadPdf` with the three new functions**

In `app/static/js/api.js`, replace the `uploadPdf` function (lines 6-41) with:

```javascript
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
```

Leave the existing `parseEvent`, `readRegion`, `exportFile`, and `health` functions unchanged.

- [ ] **Step 2: Verify the module parses (no syntax errors)**

Run: `node --check app/static/js/api.js`
Expected: no output (exit 0). `parseEvent` is still defined in the file and referenced by `runExtraction`.

- [ ] **Step 3: Commit**

```bash
git add app/static/js/api.js
git commit -m "feat: split frontend api into savePdf + runExtraction + deleteSession

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Confirm overlay markup + Stop button

**Files:**
- Modify: `app/static/index.html:139-150` (the `#plan-extracting` header) and add `#plan-confirm`

- [ ] **Step 1: Add the `#plan-confirm` overlay and a Stop button**

In `app/static/index.html`, replace the `#plan-extracting` block (lines 139-150) with the confirm overlay followed by the extracting overlay (which now has a Stop button in its header):

```html
        <div id="plan-confirm" hidden>
          <div class="inner">
            <div class="title">Use this drawing?</div>
            <div class="cf-file" id="cf-file">—</div>
            <div class="hint" id="cf-pages">—</div>
            <div class="cf-actions">
              <button class="btn ghost" id="cf-cancel">Cancel</button>
              <button class="btn primary" id="cf-start">
                <svg class="icon" width="14" height="14"><use href="#i-check"/></svg>
                Start extraction
              </button>
            </div>
          </div>
        </div>

        <div id="plan-extracting" hidden>
          <div class="inner">
            <div class="ex-head">
              <span class="ex-spinner" aria-hidden="true"></span>
              <div class="ex-head-text">
                <div class="title" id="ex-title">Extracting…</div>
                <div class="hint" id="ex-detail">Starting…</div>
              </div>
              <button class="btn ghost" id="ex-stop" title="Stop extraction">
                <svg class="icon" width="14" height="14"><use href="#i-x"/></svg>
                Stop
              </button>
            </div>
            <ol class="ex-steps" id="ex-steps"></ol>
          </div>
        </div>
```

- [ ] **Step 2: Verify the new element ids exist**

Run: `grep -n "plan-confirm\|cf-file\|cf-pages\|cf-start\|cf-cancel\|ex-stop" app/static/index.html`
Expected: one line per id (`plan-confirm`, `cf-file`, `cf-pages`, `cf-start`, `cf-cancel`, `ex-stop`).

- [ ] **Step 3: Commit**

```bash
git add app/static/index.html
git commit -m "feat: add confirm overlay and stop button to plan viewport

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Confirm/Start/Stop state machine in `main.js`

**Files:**
- Modify: `app/static/js/main.js` (imports, `handleFile`, new confirm/start/stop functions, wiring)

- [ ] **Step 1: Update the api import**

In `app/static/js/main.js`, change the api import (line 4) from:

```javascript
import { uploadPdf, exportFile, health } from './api.js';
```

to:

```javascript
import { savePdf, runExtraction, deleteSession, exportFile, health } from './api.js';
```

- [ ] **Step 2: Add module-level state and a wiring call**

Directly below the imports (after line 11, before `function init()`), add:

```javascript
// Pending upload awaiting confirmation, and the controller for the live run.
let pendingUpload = null;   // { session_id, fileName }
let extractAbort = null;    // AbortController while extraction is streaming
```

Inside `init()` (after `wireFileInputs();`, line 23), add:

```javascript
  wireExtractionControls();
```

- [ ] **Step 3: Replace `handleFile` with the save → confirm flow**

Replace `handleFile` (lines 63-89) with:

```javascript
async function handleFile(file) {
  if (!file) return;
  if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
    toast({ kind: 'warn', title: 'Unsupported file', msg: 'Please drop a .pdf' });
    return;
  }
  setBusy(`Opening ${file.name}…`);
  try {
    const meta = await savePdf(file);
    pendingUpload = { session_id: meta.session_id, fileName: meta.fileName || file.name };
    showConfirm(pendingUpload.fileName, meta.pages);
  } catch (err) {
    toast({ kind: 'error', title: 'Could not open PDF', msg: String(err.message || err) });
  } finally {
    setIdle();
  }
}
```

- [ ] **Step 4: Add the confirm/start/stop functions**

Add these immediately after `handleFile` (before the `// ===== Extraction status overlay` comment, line 91):

```javascript
// ===== Confirm → Start → Stop ======================================
function wireExtractionControls() {
  document.getElementById('cf-start').addEventListener('click', startExtraction);
  document.getElementById('cf-cancel').addEventListener('click', cancelConfirm);
  document.getElementById('ex-stop').addEventListener('click', stopExtraction);
}

function showConfirm(fileName, pages) {
  document.getElementById('plan-empty').hidden = true;
  document.getElementById('plan-extracting').hidden = true;
  document.getElementById('cf-file').textContent = fileName;
  document.getElementById('cf-pages').textContent =
    `${pages} page${pages === 1 ? '' : 's'} · ready to extract`;
  document.getElementById('plan-confirm').hidden = false;
}

function backToEmpty() {
  document.getElementById('plan-confirm').hidden = true;
  document.getElementById('plan-extracting').hidden = true;
  document.getElementById('plan-empty').hidden = false;
}

function cancelConfirm() {
  if (pendingUpload) deleteSession(pendingUpload.session_id);
  pendingUpload = null;
  backToEmpty();
}

async function startExtraction() {
  if (!pendingUpload) return;
  const { session_id, fileName } = pendingUpload;
  document.getElementById('plan-confirm').hidden = true;
  showExtracting(fileName);
  setBusy(`Extracting from ${fileName}…`);
  extractAbort = new AbortController();
  try {
    const data = await runExtraction(session_id, onExtractProgress, extractAbort.signal);
    data.fileName = fileName;
    extractStepsDone();
    setSession(data);          // viewer swaps in the page image, hides overlays
    hideExtracting();
    setIdle();
    pendingUpload = null;
    extractAbort = null;
    const charsN = data.rows.length;
    toast({
      kind: 'ok',
      title: `Loaded ${fileName}`,
      msg: `${charsN} characteristic${charsN === 1 ? '' : 's'} extracted`,
    });
  } catch (err) {
    hideExtracting();
    setIdle();
    extractAbort = null;
    if (err.name === 'AbortError') {   // user pressed Stop — session already cleaned up
      backToEmpty();
      return;
    }
    deleteSession(session_id);
    pendingUpload = null;
    backToEmpty();
    toast({ kind: 'error', title: 'Could not extract', msg: String(err.message || err) });
  }
}

function stopExtraction() {
  if (extractAbort) extractAbort.abort();   // rejects runExtraction with AbortError
  if (pendingUpload) deleteSession(pendingUpload.session_id);
  pendingUpload = null;
}
```

- [ ] **Step 5: Verify the module parses**

Run: `node --check app/static/js/main.js`
Expected: no output (exit 0).

- [ ] **Step 6: Commit**

```bash
git add app/static/js/main.js
git commit -m "feat: confirm/start/stop extraction state machine

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Style the confirm card and Stop button

**Files:**
- Modify: `app/static/styles/components.css` (after the `#plan-extracting` block, around line 207)

- [ ] **Step 1: Add styles**

In `app/static/styles/components.css`, add after the `@keyframes spin` rule (line 207). The confirm overlay reuses the `#plan-empty` centering/`.inner` card look; the Stop button is pushed to the right of the extracting header:

```css
/* Confirm-before-extract state ------------------------------------- */
#plan-confirm {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: grid;
  place-items: center;
  pointer-events: none;
  background: rgba(10, 13, 16, 0.45);
  backdrop-filter: blur(1px);
}
#plan-confirm .inner {
  pointer-events: auto;
  min-width: 320px;
  max-width: 420px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-lg);
  padding: 24px 28px;
  background: rgba(15, 20, 25, 0.55);
  text-align: center;
}
#plan-confirm .inner .title {
  color: var(--fg);
  font-weight: 500;
  font-size: var(--fs-lg);
  margin-bottom: 10px;
}
#plan-confirm .cf-file {
  color: var(--fg);
  font-family: var(--font-mono);
  font-size: var(--fs-sm);
  word-break: break-all;
  margin-bottom: 2px;
}
#plan-confirm .hint {
  color: var(--fg-muted);
  font-size: var(--fs-sm);
}
#plan-confirm .cf-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
  margin-top: 20px;
}

/* Stop button sits at the far right of the extracting header */
#plan-extracting .ex-head #ex-stop {
  margin-left: auto;
  flex: none;
  align-self: flex-start;
}
```

- [ ] **Step 2: Manual verification in a browser**

Start the app: `.venv/bin/uvicorn app.main:app --reload`
Then in a browser at `http://127.0.0.1:8000`:

1. Drop or open a PDF → the "Upload a technical drawing" card is replaced by the **confirm card** showing the filename + page count, with **Cancel** / **Start extraction**.
2. Click **Cancel** → returns to the upload card. (Network tab shows a `DELETE /api/session/{id}`.)
3. Open the PDF again → **Start extraction** → the live SSE step list appears with a **Stop** button at the top-right of the header; steps tick through render → notes → detect → ocr → place.
4. Click **Stop** mid-run → returns to the upload card; a `DELETE /api/session/{id}` fires and the server logs show the worker stopped (no further progress). No error toast appears.
5. Let a run finish without stopping → the page image loads and the characteristics table fills, exactly as before.

Expected: all five behaviors as described.

- [ ] **Step 3: Commit**

```bash
git add app/static/styles/components.css
git commit -m "feat: style confirm card and stop button

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Run the full backend test suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass.

- [ ] **Confirm the manual browser walkthrough (Task 7, Step 2) passes end-to-end**, including a real Stop mid-OCR freeing the backend for an immediate second extraction.
