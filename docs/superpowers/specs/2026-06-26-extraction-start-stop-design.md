# Extraction Start/Stop Gate — Design

**Date:** 2026-06-26
**Status:** Approved

## Problem

Today, selecting or dropping a PDF *immediately* auto-starts extraction. The
"Upload a technical drawing" card (`#plan-empty`) is replaced by a live SSE step
overlay (`#plan-extracting`), but the user never gets to confirm the file, and
there is no way to stop a run once it begins. Extraction is CPU/GPU-bound and can
take a while, so an accidental upload — or the wrong file — wastes the shared
OCR/VLM backend until it finishes.

## Goal

Insert a **confirm → Start → (live SSE) → Stop** gate between file selection and
extraction:

1. After a PDF is selected/dropped, show a confirmation card (filename + page
   count) with **Start** and **Cancel** buttons. Nothing is extracted yet.
2. **Start** begins extraction and shows the live SSE step progress, now with a
   **Stop** button.
3. **Stop** terminates the run mid-flight — including the server-side compute —
   freeing the shared backend.
4. After Stop, Cancel, or an error, the UI returns to the file-select state and
   the abandoned session is discarded.

## Decisions (from brainstorming)

- **Stop = true cancellation.** The server worker is signalled to stop and the
  pipeline unwinds cooperatively; it does not keep computing in the background.
- **Confirm card shows filename + page count** (no thumbnail). Page count is a
  cheap `fitz.open(pdf).page_count`; no page render before commit.
- **Post-stop lands on file-select** (`#plan-empty`), so the user can pick the
  same or a different PDF and start again.

## Architecture

### Plan-viewport state machine

Three overlay states swap inside `#plan-viewport` (today there are two):

| State | Element | Shows |
|-------|---------|-------|
| File select | `#plan-empty` | "Upload a technical drawing" + Open PDF (landing & return state) |
| Confirm | `#plan-confirm` *(new)* | Filename + page count, **Start** (primary) / **Cancel** |
| Extracting | `#plan-extracting` *(reworked)* | Live SSE step list + **Stop** button in header |

Transitions:

```
empty ──select/drop PDF──▶ [POST /api/upload] ──▶ confirm
confirm ──Start──▶ [POST /api/extract/{id} SSE] ──▶ extracting
extracting ──result──▶ loaded (session set, overlays hidden)
confirm ──Cancel──────────┐
extracting ──Stop/error──▶ ┴─▶ [DELETE /api/session/{id}] ──▶ empty
```

### Backend: split upload from extract

The current `/api/upload` does two jobs (save + extract). Split them:

- **`POST /api/upload`** — *no extraction*. Creates a session dir, writes
  `input.pdf`, returns `{ session_id, fileName, pages }`. `pages` from
  `fitz.open(pdf_path).page_count`. Instant; powers the confirm card.

- **`POST /api/extract/{session_id}`** *(new)* — the SSE stream that currently
  lives in `/api/upload`. Validates the session id (existing `_SESSION_RE`
  guard) and that `input.pdf` exists (404 otherwise). Runs `extract()` in the
  worker thread; streams `progress` / `result` / `error` events exactly as
  today. The `result` payload is unchanged (`session_id`, `image_url`, `rows`,
  `notes`).

- **`DELETE /api/session/{session_id}`** *(new)* — removes the session dir.
  Called by the frontend on Cancel and Stop. Idempotent: missing dir → 204/200,
  not an error.

### Cancellation

Cooperative, signalled through a `threading.Event`:

1. The extract handler creates `cancel = threading.Event()` and passes a
   `progress` callback to the worker that **checks `cancel.is_set()` before
   forwarding each event and raises `CancelledError` if set**.
2. `extract()` calls `emit(...)` at every step boundary and once per region
   during OCR (the long phase — `extract.py:137`), so the raise unwinds the
   pipeline within at most one region's OCR time.
3. The SSE stream loop detects client disconnect: instead of a pure blocking
   `events.get()`, it polls with a short timeout and, on each empty tick, checks
   `await request.is_disconnected()`. On disconnect it sets `cancel`, stops
   streaming, and lets the worker unwind. The worker's `CancelledError` is
   caught and ignored (no client to receive an error event).
4. On cancellation the extract handler removes the session dir (belt-and-braces
   alongside the frontend `DELETE`).

The worker's `progress` callback gains a cancel check; this hook is unit-testable
without a live HTTP disconnect.

### Frontend

- **`api.js`**
  - `savePdf(file)` — replaces the save half of `uploadPdf`; POST `/api/upload`,
    returns `{ session_id, fileName, pages }`.
  - `runExtraction(sessionId, onProgress, signal)` — opens
    `POST /api/extract/{id}` with `{ signal }` from an `AbortController`; parses
    the SSE stream (the existing reader/`parseEvent` logic moves here); resolves
    to the final result. An aborted fetch rejects with `AbortError`, which the
    caller treats as a user-initiated stop (not an error toast).
  - `deleteSession(id)` — `DELETE /api/session/{id}`; fire-and-forget.
  - `uploadPdf` is removed (split into the two above).

- **`main.js`**
  - `handleFile(file)` — validates type, calls `savePdf`, then `showConfirm(meta)`
    instead of auto-extracting. Stores the pending `{ session_id, fileName }`.
  - `startExtraction()` — Start button. Creates an `AbortController`, swaps to
    `#plan-extracting`, calls `runExtraction(id, onExtractProgress, signal)`. On
    resolve → `setSession(data)` (unchanged success path + toast). On
    `AbortError` → silent return to empty. On other error → error toast + return
    to empty.
  - `stopExtraction()` — Stop button. `controller.abort()`, `deleteSession(id)`,
    return to `#plan-empty`.
  - `cancelConfirm()` — Cancel button. `deleteSession(id)`, return to
    `#plan-empty`.
  - The existing `showExtracting` / `hideExtracting` / `onExtractProgress` /
    `EXTRACT_STEPS` step-list logic is reused unchanged.

- **Markup (`index.html`)**
  - New `#plan-confirm` overlay sibling to `#plan-empty` / `#plan-extracting`,
    using the existing `.inner` card layout: a title (filename), a hint (page
    count), and a button row with `.btn.primary` Start + `.btn.ghost` Cancel.
  - A **Stop** button (`.btn.ghost`) added to the `#plan-extracting` `.ex-head`.

- **Styling (`components.css`)** — reuse `.inner`, `.btn`, `.ex-head`. Minor
  additions only (button row spacing). No new design system work.

## Error handling

- Bad/oversized/non-PDF file at `handleFile`: existing client-side type guard;
  server returns 4xx → toast, stay on empty.
- `/api/extract` with unknown session id or missing `input.pdf`: 404 → toast,
  return to empty.
- Extraction failure inside the pipeline: `error` SSE event → toast, return to
  empty, delete session.
- `AbortError` (user Stop): no toast; silent return to empty.
- `DELETE` on a missing session: idempotent success.

## Testing

`tests/test_api.py` (extends existing coverage):

- `/api/upload` returns `{ session_id, fileName, pages }` and writes `input.pdf`
  **without** producing extraction artifacts (no `page.png`, no rows).
- `/api/extract/{id}` streams `progress` events and a terminal `result` with the
  expected shape; matches the pre-split behavior.
- `/api/extract/{bad_id}` → 404; `/api/extract/{id}` with no saved PDF → 404.
- `DELETE /api/session/{id}` removes the dir; deleting a missing session is not
  an error.
- **Cancellation unit test:** a `progress` callback wired to a set
  `threading.Event` raises `CancelledError`, and feeding it into `extract()`
  causes the call to stop before completion (assert it raises / does not return
  a full result). No live HTTP disconnect required.

## Out of scope (YAGNI)

- First-page thumbnail in the confirm card.
- Multi-page selection / page range.
- Pause/resume (only Start and hard Stop).
- Progress persistence across reloads.
