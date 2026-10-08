// Entry point: wires modules, health check, file upload, the queue wait,
// reopening a drawing from its link, and Finish & Export.

import { state, on, restoreSession, clearSession, undo, redo,
         canUndo, canRedo, progress, canFinish } from './state.js';
import { getDocument, cancelDocument, openDocument, deleteDocument, downloadExport,
         health, postEvents, beaconEvents, sealSession, createBatch, getBatch,
         startBatch, discardBatch, setIncluded, listDocuments, moveToFront,
         reextractDocument, retryDocument, pauseQueue, resumeQueue } from './api.js';
import { uploadFile, UploadError, forgetDraft, forgetDraftsOf, importFiles } from './upload.js';
import { importSingle, stagesOnly } from './importone.js';
import { badgesFor, workListBadges } from './badges.js';
import { createJournal } from './journal.js';
import { createFinisher, createExporter } from './finish.js';
import { restoreReview, flushBeforeLeaving, afterFlush } from './reopen.js';
import { nextStopStep } from './stop.js';
import { initViewer } from './viewer.js';
import { initTable } from './table.js';
import { initWorkList } from './worklist.js';
import { stagingUrl } from './router.js';
import {
  toast, initModal, openHelp, initSplitter,
  initThemeAndDensity, initDragDrop,
} from './ui.js';
import { initShortcuts } from './shortcuts.js';

// The drawing this tab is waiting on (staged or extracting). Extraction runs
// on the server; closing the tab no longer stops it (batch design §1).
// row: its staging row; file: the File it was imported from in THIS tab
// (absent when reached by link), so Start / Cancel can forget its draft.
let waitingOn = null;     // { id, batchId, fileName, pages, row, file }
let uploading = false;    // a file is on its way to the server (chunked, batch design §2)
let pollTimer = null;     // the wait-for-ready poll only
let stopTimer = null;     // Stop's cancel/remove loop -- its own timer, so a late poll can never kill it
let stopping = false;     // Stop pressed; waiting for the worker to let go
let stopGaveUp = false;   // removal kept failing; the Stop button now closes the overlay
let resuming = false;     // the #doc link is still being opened at page load
let opening = 0;          // loadForReview calls in progress (a count, so an overlap cannot clear it early)
let finishing = false;    // a Finish is running: never switch drawings under it
let staleShown = false;   // "opened in another tab" already said for this drawing
// A ready drawing the reviewer chose NOT to switch to yet, because the open
// one's changes had not saved. It opens by itself once they have
// (afterFlush); until then it is the one drawing this tab is waiting on.
let pendingOpen = null;   // id
let closePendingToast = null;
let workList = null;      // initWorkList()'s handle; Task 13 reads it back via workList.getDocs()

// The review journal: every state operation, numbered and flushed to the
// server (docs/plans/2026-10-07-hitl-review-grading-design.md §2). It is
// mandatory (batch design decision 30), so there is no "logging off" mode.
const journal = createJournal({
  send: (sid, writer, events) => postEvents(sid, writer, events),
});
let flushTimer = null;
let flushBackoff = 500;

// Flush → seal → export, single-flight, never re-sealing an unchanged
// journal (finish.js) -- including after a reload, via seed() on open.
const finisher = createFinisher({
  journal,
  canFinish,
  seal: (sid, body) => sealSession(sid, body),
  // Remembers which file of a revision already arrived, so a retry after a
  // failed PDF does not download the spreadsheet a second time.
  exportAll: createExporter((sid, kind, revision) => downloadExport(sid, kind, revision)),
});

function init() {
  initThemeAndDensity();
  initSplitter();
  initModal();
  initViewer();
  initTable();
  initShortcuts();
  initDragDrop(handleFile);
  wireHeader();
  wireFooter();
  wireFileInputs();
  wireExtractionControls();
  wireFinish();
  wireJournal();
  wireUndoRedo();
  pingHealth();
  workList = initWorkList({
    listDocuments, moveToFront, cancelDocument, retryDocument, deleteDocument,
    downloadExport, pauseQueue, resumeQueue, toast, badgesFor: workListBadges,
  });
  wireImport();
  resumeFromHash();
}

// ===== Link / reload =================================================
// #doc=<id> keeps the drawing addressable, so a reload -- or coming back
// tomorrow with the same link -- reopens it where the reviewer stopped.
function setHash(id) { history.replaceState(null, '', id ? `#doc=${id}` : '#'); }
function hashDoc() { return /^#doc=([0-9a-f]{32})$/.exec(location.hash)?.[1] || null; }

async function resumeFromHash() {
  const id = hashDoc();
  if (!id) return;
  resuming = true;   // set before the first await: an upload now would race the link
  try {
    const doc = await getDocument(id);
    await routeDocument(doc);
  } catch (err) {
    // Only a drawing that is really gone loses its link; a hiccup keeps it
    // so a reload tries again.
    if (err?.status === 404) {
      setHash(null);
      toast({ kind: 'warn', title: 'Drawing not found', msg: 'The drawing in this link was removed.' });
    } else {
      toast({ kind: 'error', title: 'Could not reach the drawing',
              msg: `${String(err?.message || err)} — reload the page to try again.` });
    }
  } finally {
    resuming = false;
  }
}

async function routeDocument(doc) {
  waitingOn = { id: doc.id, batchId: doc.batch_id, fileName: doc.file_name, pages: doc.pages };
  if (doc.state === 'staged') await showStaged(doc);
  else if (doc.state === 'queued' || doc.state === 'extracting') { showExtracting(doc.file_name); pollUntilReady(); }
  else if (['ready', 'in_review', 'finished'].includes(doc.state)) await loadForReview(doc.id);
  else {
    toast({ kind: 'error', title: `Drawing ${doc.state}`,
            msg: doc.error_type ? `Extraction failed (${doc.error_type}).` : 'Upload it again to retry.' });
    waitingOn = null;
    hideExtracting();
    backToEmptyOrCurrent();
  }
}

// A staged drawing reached from its link: the confirm screen needs its
// staging row (badges, Include) from the import it sits in. On failure the
// tab must not keep waiting on it, or no upload would ever be accepted.
async function showStaged(doc) {
  let row;
  try {
    row = (await getBatch(doc.batch_id)).documents.find((r) => r.id === doc.id);
  } catch (err) {
    waitingOn = null;
    throw err;
  }
  if (!row) {
    waitingOn = null;
    throw Object.assign(new Error('Drawing not found'), { status: 404 });
  }
  waitingOn.row = row;
  showConfirm(row);
}

// After backing out of a drawing: the review still on screen (a drawing
// dropped while reviewing another), else the empty state.
function backToEmptyOrCurrent() {
  setHash(state.sessionId || null);
  if (state.sessionId) {
    document.getElementById('plan-confirm').hidden = true;
    document.getElementById('plan-extracting').hidden = true;
  } else {
    backToEmpty();
  }
}

// ===== Header / file chip ============================================
function wireHeader() {
  document.getElementById('file-close').addEventListener('click', () => {
    if (!confirm('Close this drawing? Your review is saved and reopens from its link.')) return;
    // clearSession() below wipes sessionId/writer out of the journal's
    // reach (start() on the NEXT drawing resets it anyway) -- so any event
    // still only in `pending` must go out now or it is gone for good. The
    // events are idempotent server-side (append is by seq), so a close that
    // races an in-flight flush double-sends at worst, never drops.
    if (journal.active && journal.pending.length) {
      beaconEvents(journal.sessionId, journal.writer, journal.pending);
    }
    clearSession();
    setHash(null);
    // A drawing was waiting for this one's changes to save. Closing is the
    // reviewer leaving anyway (the beacon above was the last try), so open
    // it now rather than leave it waiting on a drawing nobody is reviewing.
    if (pendingOpen) loadForReview(pendingOpen, { skipFlush: true });
  });
  on('session', () => {
    const chip  = document.getElementById('file-chip');
    const name  = document.getElementById('file-name');
    if (state.sessionId) {
      chip.hidden = false;
      name.textContent = state.fileName || 'drawing.pdf';
      document.getElementById('finish-btn').disabled = false;
    } else {
      chip.hidden = true;
      const fin = document.getElementById('finish-btn');
      fin.disabled = true;
      // The 'change' handler skips a cleared session, so reset here or the
      // next drawing opens with the last one's "N flagged rows" state.
      fin.classList.remove('blocked');
      fin.title = 'Seal the review and export Excel + ballooned PDF';
    }
  });
}

function wireFooter() {
  document.getElementById('help-toggle').addEventListener('click', openHelp);
}

// ===== File input handling ==========================================
function wireFileInputs() {
  const main  = document.getElementById('file-input');
  const empty = document.getElementById('file-input-empty');
  // The value is cleared once read: a picker still holding a file fires no
  // 'change' when that same file is chosen again -- and choosing it again
  // is exactly how an interrupted upload resumes.
  const pick = (e) => {
    const file = e.target.files[0];
    e.target.value = '';
    if (file) handleFile(file);
  };
  main.addEventListener('change', pick);
  empty.addEventListener('change', pick);
}

async function handleFile(file) {
  if (!file) return;
  // One waiting drawing per tab: a second upload would orphan the first,
  // which only its link could ever reach again (B3 adds a list). Each
  // refusal names a control the reviewer actually has on screen.
  if (resuming) {
    toast({ kind: 'warn', title: 'Still opening the linked drawing', msg: 'Try again in a moment.' });
    return;
  }
  if (opening) {
    toast({ kind: 'warn', title: 'A drawing is opening', msg: 'Try again in a moment.' });
    return;
  }
  if (pendingOpen) {
    toast({ kind: 'warn', title: 'A drawing is waiting to open',
            msg: 'It opens once this drawing\'s changes are saved — or close this drawing (×) to open it now.' });
    return;
  }
  if (waitingOn) {
    toast({ kind: 'warn', title: 'A drawing is already waiting',
            msg: stopGaveUp ? 'Close the stopped drawing first.'
               : stopping ? 'It is still stopping — try again once it has.'
               : 'Cancel or stop it before opening another drawing.' });
    return;
  }
  if (uploading) {
    toast({ kind: 'warn', title: 'A drawing is uploading', msg: 'Try again once it has arrived.' });
    return;
  }
  // One PDF at a time on this interim screen: a ZIP stages many drawings,
  // which only B3's staging view can show.
  if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
    toast({ kind: 'warn', title: 'Unsupported file', msg: 'Please drop a .pdf' });
    return;
  }
  uploading = true;
  setBusy(`Uploading ${file.name}…`);
  // The draft memory and its fallbacks live in importone.js (node-tested):
  // remembered before the first byte, resumed when the same file is
  // selected again, discarded only on a fatal failure.
  const storage = (() => { try { return window.localStorage; } catch { return null; } })();
  try {
    const out = await importSingle({
      file, storage, createBatch, getBatch, discardBatch,
      uploadFile,
      onProgress: (sent, total) =>
        setBusy(`Uploading ${file.name}… ${total ? Math.floor((100 * sent) / total) : 100}%`),
    });
    if (out.unreadable) {
      toast({ kind: 'error', title: 'Could not read the PDF',
              msg: 'The file is damaged or password-protected, so it cannot be extracted.' });
      return;
    }
    const { row } = out;
    waitingOn = { id: row.id, batchId: out.batchId, fileName: row.file_name, pages: row.pages, row, file };
    setHash(row.id);
    showConfirm(row);
  } catch (err) {
    if (err instanceof UploadError && !err.fatal) {
      // The server or the network is away for now (the client already
      // retried for minutes). The draft and what arrived of the file stay.
      toast({ kind: 'warn', title: 'Upload interrupted',
              msg: 'Select the same file again to continue where it stopped.' });
    } else if (err?.reason === 'file_unreadable') {
      // Moved, changed or locked since it was chosen. What arrived of it
      // is kept (importone.js) if the import had begun before.
      toast({ kind: 'error', title: 'Could not read the file',
              msg: 'It was moved or changed since it was chosen. Select it again.' });
    } else {
      // Refused, disk full, or a fault before any upload began: the draft
      // is already discarded -- nothing worth resuming.
      toast({ kind: 'error', title: 'Could not upload the PDF', msg: String(err?.message || err) });
    }
  } finally {
    uploading = false;
    setIdle();
  }
}

// ===== Work list: the `+ Import` entry point ========================
// ZIP + folder (webkitdirectory, a cheap follow-on the input also accepts
// via its own attribute set elsewhere) + plain files all land here as a
// multi-file select, imported in parallel into one new draft batch, then
// routed to its staging view. `stagingUrl` + a plain `setHash` is used
// rather than the router's `go()`/`navigate()` seam: Task 13 owns the
// hashchange-driven dispatch and is expected to land after this task in a
// strict reading of the plan's order, so wiring `go()` here would mean
// using a function this task does not define. Task 13 can replace this
// `setHash(stagingUrl(batchId))` with `go(stagingUrl(batchId))` once it
// exists, with no other change needed here.
function wireImport() {
  document.getElementById('wl-import')?.addEventListener('click', () => {
    document.getElementById('wl-import-input')?.click();
  });
  document.getElementById('wl-import-input')?.addEventListener('change', async (e) => {
    const files = [...e.target.files];
    e.target.value = '';   // a picker holding the same files fires no 'change' on a re-pick otherwise
    if (!files.length) return;
    setBusy('Importing…');
    try {
      const name = files.length === 1 ? files[0].name
        : new Date().toLocaleString();   // createBatch's own UTC fallback is used if this is omitted entirely
      const { id: batchId } = await createBatch(name);
      const results = await importFiles(files, { batchId });
      const failed = results.filter((r) => !r.ok).length;
      if (failed) {
        // A disk-full (507) or any other fatal failure lands here too --
        // the staging view's own Discard button (Task 12) is the recovery
        // for a draft that can never complete, so this points at it rather
        // than retrying forever itself.
        toast({ kind: 'warn', title: 'Some files did not import',
                msg: `${failed} of ${files.length} failed. Open the import to retry or discard it.` });
      }
      // Not setHash(): that helper still writes B1's `#doc=<id>` shape
      // (Task 13 replaces it with the router's `go()`). The staging URL is
      // the new `#/staging/<batch>` scheme straight from router.js, written
      // directly so this task does not depend on a function it does not own.
      history.replaceState(null, '', stagingUrl(batchId));
    } catch (err) {
      toast({ kind: 'error', title: 'Could not start the import', msg: String(err?.message || err) });
    } finally {
      setIdle();
    }
  });
}

// ===== Confirm → Queue → Wait → Open ================================
function wireExtractionControls() {
  document.getElementById('cf-start').addEventListener('click', startExtraction);
  document.getElementById('cf-cancel').addEventListener('click', cancelConfirm);
  document.getElementById('ex-stop').addEventListener('click', stopExtraction);
}

function showConfirm(row) {
  document.getElementById('plan-empty').hidden = true;
  document.getElementById('plan-extracting').hidden = true;
  document.getElementById('cf-file').textContent = row.file_name;
  document.getElementById('cf-pages').textContent =
    `${row.pages} page${row.pages === 1 ? '' : 's'} · ready to extract`;
  // The staging warnings (multi-page, oversized, duplicate) -- as TEXT: a
  // duplicate's badge quotes another drawing's file and batch names. A file
  // they excluded by default is started with "Include and start", which
  // says what the press does: it is the explicit permission decision 26
  // asks for.
  const warn = document.getElementById('cf-warn');
  const badges = badgesFor(row);
  warn.textContent = badges.map((b) => b.text).join(' · ');
  warn.hidden = badges.length === 0;
  setStartLabel(row.included ? 'Start extraction' : 'Include and start');
  document.getElementById('plan-confirm').hidden = false;
}

// The button's last text node is its label (index.html: icon, then text).
function setStartLabel(text) {
  const btn = document.getElementById('cf-start');
  const node = [...btn.childNodes].reverse().find((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
  if (node) node.textContent = ` ${text} `;
}

function backToEmpty() {
  document.getElementById('plan-confirm').hidden = true;
  document.getElementById('plan-extracting').hidden = true;
  document.getElementById('plan-empty').hidden = false;
}

// The draft is no longer one to resume into (started or discarded). Only
// known for a file imported in this tab; a stale memory left behind is
// harmless -- the next import of that file finds the draft gone or started
// and starts a fresh one (importone.js).
function forgetImported(w) {
  if (!w) return;
  const storage = (() => { try { return window.localStorage; } catch { return null; } })();
  // Reached by its link, there is no File to forget by: every memory
  // pointing at the draft goes instead.
  if (w.file) forgetDraft(storage, w.file);
  else forgetDraftsOf(storage, w.batchId);
}

function cancelConfirm() {
  // The whole batch of one goes: its staged file and the draft itself.
  // (Interim only -- B3's staging view removes single rows of a larger draft.)
  if (waitingOn) {
    forgetImported(waitingOn);
    discardBatch(waitingOn.batchId);
  }
  waitingOn = null;
  backToEmptyOrCurrent();
}

async function startExtraction() {
  const w = waitingOn;
  if (!w) return;
  document.getElementById('plan-confirm').hidden = true;
  showExtracting(w.fileName);
  try {
    // Start queues EVERY included drawing of the draft, and this screen
    // shows one. A second copy (an earlier attempt's, or another tab's)
    // would be extracted unseen, so Start is refused unless the draft holds
    // exactly the drawing on screen.
    const view = await getBatch(w.batchId);
    if (waitingOn !== w || stopping) return;
    if (!stagesOnly(view, w.id)) {
      hideExtracting();
      showConfirm(w.row);
      toast({ kind: 'error', title: 'Cannot start this import',
              msg: 'It holds more than this drawing (an earlier attempt or another tab). '
                 + 'Cancel it and select the file again.' });
      return;
    }
    // Excluded by default (out of scope or a duplicate): the press of
    // "Include and start" is the user including it (decision 26).
    if (w.row && !w.row.included) w.row = await setIncluded(w.id, true);
    // The check above ran a request ago; [w.id] makes the server repeat it
    // at the moment of Start, so a copy staged in between is refused too.
    await startBatch(w.batchId, [w.id]);
    forgetImported(w);
    if (waitingOn !== w || stopping) return;   // Stop pressed meanwhile: Stop owns it now
    pollUntilReady();
  } catch (err) {
    if (waitingOn !== w || stopping) return;
    // The reply may have been lost after the server included it or started
    // the import. Ask before offering Start again: a drawing that is no
    // longer staged is on its way, so wait for it like any other.
    try {
      const doc = await getDocument(w.id);
      if (waitingOn !== w || stopping) return;
      if (doc.state !== 'staged') { forgetImported(w); pollUntilReady(); return; }
    } catch { /* unknown: offer Start again; a second Start is refused harmlessly */ }
    if (waitingOn !== w || stopping) return;
    hideExtracting();
    // Still staged on the server: offer it again rather than strand it.
    showConfirm(w.row);
    toast({ kind: 'error', title: 'Could not start', msg: String(err?.message || err) });
  }
}

// Poll, not stream: the run belongs to the server, and a poll has nothing
// to reconnect after a sleep or a dropped network (decision 8).
function pollUntilReady() {
  if (!waitingOn || stopping) return;
  clearTimeout(pollTimer);
  pollTimer = setTimeout(async () => {
    if (!waitingOn || stopping) return;
    let doc;
    try {
      doc = await getDocument(waitingOn.id);
    } catch {
      if (!waitingOn || stopping) return;
      pollUntilReady();
      return;
    }
    if (!waitingOn || stopping) return;   // Stop pressed while the request was out
    if (doc.state === 'queued') {
      document.getElementById('ex-detail').textContent =
        doc.queue_position > 1 ? `Waiting — ${doc.queue_position - 1} drawing(s) ahead` : 'Waiting for the worker…';
      pollUntilReady();
    } else if (doc.state === 'extracting') {
      if (doc.progress) onExtractProgress(doc.progress);
      pollUntilReady();
    } else {
      extractStepsDone();
      hideExtracting();
      routeDocument(doc);
    }
  }, 1500);
}

// Cancel is cooperative: the worker stops at its next progress report, and
// detection reports none while it runs -- so this can take many minutes,
// and the copy must not promise an immediate stop. The drawing is removed
// once the worker lets go (DELETE is refused until then); the interim UI
// has no list to come back to (B3 adds one).
async function stopExtraction() {
  if (stopGaveUp) { closeStopOverlay(); return; }
  if (!waitingOn || stopping) return;
  stopping = true;
  clearTimeout(pollTimer);
  const { id } = waitingOn;
  document.getElementById('ex-stop').disabled = true;
  document.getElementById('ex-title').textContent = 'Cancelling…';
  document.getElementById('ex-detail').textContent =
    'Stops at the next step — this can take a few minutes.';
  removeWhenStopped(id, 0, await tryCancel(id));
}

// Whether the server accepted the cancel. It refuses one for a drawing that
// is not queued/extracting (it finished first, or is still staged) -- DELETE
// then simply succeeds -- and a network failure leaves it unknown, so the
// loop below keeps trying until it is accepted or the drawing is gone.
async function tryCancel(id) {
  try { await cancelDocument(id); return true; } catch { return false; }
}

// A 409 (still stopping) is waited out for as long as it takes; other
// failures in a row give up visibly after a few (stop.js), leaving a way out.
function removeWhenStopped(id, failures = 0, cancelled = false) {
  clearTimeout(stopTimer);
  stopTimer = setTimeout(async () => {
    if (!cancelled) cancelled = await tryCancel(id);
    const next = nextStopStep(await deleteDocument(id), failures);
    if (next.step === 'wait' || next.step === 'retry') {
      if (next.step === 'retry') {
        document.getElementById('ex-detail').textContent = cancelled
          ? 'Cancelled — the server has not removed it yet; still trying…'
          : 'The server has not confirmed the cancel yet; still trying…';
      }
      removeWhenStopped(id, next.failures, cancelled);
      return;
    }
    if (next.step === 'give_up') {
      stopGaveUp = true;
      document.getElementById('ex-title').textContent = 'Could not remove the drawing';
      document.getElementById('ex-detail').textContent = cancelled
        ? 'It is cancelled and will not be extracted, but the server did not remove it. Close this and carry on.'
        : 'The server confirmed neither the cancel nor the removal, so it may still be extracted. '
          + `Close this; its link is ${location.origin}${location.pathname}#doc=${id}`;
      setStopLabel('Close');
      document.getElementById('ex-stop').disabled = false;
      return;
    }
    closeStopOverlay();
    toast({ kind: 'ok', title: 'Stopped', msg: 'The drawing was removed.' });
  }, 1500);
}

function closeStopOverlay() {
  clearTimeout(stopTimer);
  clearTimeout(pollTimer);
  stopping = false;
  stopGaveUp = false;
  waitingOn = null;
  setStopLabel('Stop');
  document.getElementById('ex-stop').disabled = false;
  hideExtracting();
  backToEmptyOrCurrent();
}

// The button's last text node is its label (index.html: icon, then text).
function setStopLabel(text) {
  const btn = document.getElementById('ex-stop');
  const node = [...btn.childNodes].reverse().find((n) => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
  if (node) node.textContent = ` ${text} `;
  btn.title = text === 'Close' ? 'Close (the drawing stays cancelled)' : 'Stop extraction';
}

function confirmLeaveUnsaved() {
  return confirm('Your latest changes to the open drawing are not saved yet — '
               + 'the server has not taken them.\n\n'
               + 'OK: open the other drawing anyway (those changes may be lost).\n'
               + 'Cancel: stay here; saving keeps retrying.');
}

// `skipFlush`: the reviewer already left the open drawing (closed it), or
// its writer was taken by another tab -- asking again would be pointless.
// While a drawing is opening no upload is accepted: on the auto-open path
// waitingOn is already null, so without this a drop would slip in and its
// confirm panel would be left under the opened review with a dead Start.
async function loadForReview(id, opts) {
  opening++;
  try {
    return await openForReview(id, opts);
  } finally {
    opening--;
  }
}

async function openForReview(id, { skipFlush = false } = {}) {
  // Taken synchronously, before any await, so a second flush tick cannot
  // start a second open of the same waiting drawing. "Stay" re-sets it.
  if (pendingOpen === id) {
    pendingOpen = null;
    closePendingToast?.();
    closePendingToast = null;
  }
  // journal.resume() discards the open drawing's unflushed events, so they
  // go to the server first -- or the reviewer decides, never silence.
  if (!skipFlush && journal.active && journal.sessionId !== id) {
    if (!(await flushBeforeLeaving(journal, confirmLeaveUnsaved))) {
      // Stay -- but keep the other drawing: it opens by itself once this
      // one's changes are saved (scheduleFlush → afterFlush), and its link
      // is on screen in case this tab goes away first.
      if (waitingOn?.id === id) waitingOn = null;   // never another drawing's
      pendingOpen = id;
      hideExtracting();
      backToEmptyOrCurrent();
      closePendingToast?.();
      closePendingToast = toast({ kind: 'warn', title: 'Stayed on this drawing', timeout: 0,
        msg: 'The other drawing opens by itself once this one\'s changes are saved. '
           + `Its link: ${location.origin}${location.pathname}#doc=${id}` });
      scheduleFlush(flushBackoff);
      return;
    }
    // Left anyway: restoreReview sends whatever is still pending with this
    // drawing's writer just before the journal moves on -- including edits
    // made while /open is in flight.
  }
  setBusy('Opening drawing…');
  try {
    const data = await openDocument(id);
    // Restore, resume and seed with nothing in between: an op applied
    // between restoring and resuming would never be journaled. Throws with
    // the screen untouched or cleared, never half-built (reopen.js).
    restoreReview(data, { restoreSession, clearSession, journal, finisher, beacon: beaconEvents });
    clearTimeout(flushTimer);
    flushBackoff = 500;
    staleShown = false;
    if (waitingOn?.id === id) waitingOn = null;   // never another drawing's
    setHash(id);
    hideExtracting();
    const n = data.rows.length;
    const nEv = data.events.length;
    toast({ kind: 'ok', title: `Loaded ${data.fileName}`,
            msg: nEv ? `Review restored (${nEv} change${nEv === 1 ? '' : 's'})`
                     : `${n} characteristic${n === 1 ? '' : 's'} extracted` });
  } catch (err) {
    if (waitingOn?.id === id) waitingOn = null;   // never another drawing's
    hideExtracting();
    toast({ kind: 'error', title: 'Could not open the drawing',
            msg: state.sessionId && state.sessionId !== id
              ? `${String(err.message || err)} — its link: ${location.origin}${location.pathname}#doc=${id}`
              : `${String(err.message || err)} — reload the page to try again.` });
    if (state.sessionId) {
      backToEmptyOrCurrent();
    } else {
      // Keep the link on the drawing that failed, so a reload retries it.
      backToEmpty();
      setHash(id);
    }
  } finally {
    setIdle();
  }
}

// ===== Extraction status overlay ====================================
// Ordered pipeline steps, keyed to the `step` values the server emits.
const EXTRACT_STEPS = [
  { key: 'render', label: 'Rendering page' },
  { key: 'notes',  label: 'Reading notes block' },
  { key: 'title',  label: 'Reading title block' },
  { key: 'detect', label: 'Detecting characteristics' },
  { key: 'ocr',    label: 'Reading regions' },
  { key: 'place',  label: 'Placing balloons' },
];

function showExtracting(fileName) {
  document.getElementById('plan-empty').hidden = true;
  document.getElementById('ex-title').textContent = `Extracting ${fileName}`;
  document.getElementById('ex-detail').textContent = 'Starting…';

  const list = document.getElementById('ex-steps');
  list.innerHTML = '';
  for (const s of EXTRACT_STEPS) {
    const li = document.createElement('li');
    li.dataset.key = s.key;
    li.innerHTML =
      '<span class="ex-icon"><span class="ex-dot"></span>' +
      '<svg class="ex-check" width="14" height="14"><use href="#i-check"/></svg></span>' +
      `<span class="ex-label">${s.label}</span>`;
    list.appendChild(li);
  }
  document.getElementById('plan-extracting').hidden = false;
}

function hideExtracting() {
  document.getElementById('plan-extracting').hidden = true;
}

function onExtractProgress({ step, detail, current, total }) {
  const idx = EXTRACT_STEPS.findIndex((s) => s.key === step);
  if (idx === -1) return;

  const items = document.querySelectorAll('#ex-steps li');
  items.forEach((li, i) => {
    li.classList.toggle('done', i < idx);
    li.classList.toggle('active', i === idx);
  });

  let label = EXTRACT_STEPS[idx].label;
  if (total != null && current != null) label += ` · ${current}/${total}`;
  const active = items[idx];
  if (active) active.querySelector('.ex-label').textContent = label;
  document.getElementById('ex-detail').textContent = detail || label;
}

function extractStepsDone() {
  document.querySelectorAll('#ex-steps li').forEach((li) => {
    li.classList.remove('active');
    li.classList.add('done');
  });
  document.getElementById('ex-detail').textContent = 'Done';
}

function setBusy(label) {
  const pill = document.getElementById('status-pill');
  pill.hidden = false;
  pill.className = 'status-pill busy';
  document.getElementById('status-text').textContent = label;
}
function setIdle() {
  const pill = document.getElementById('status-pill');
  pill.hidden = true;
  pill.className = 'status-pill';
}

// ===== Journal =======================================================
function wireJournal() {
  on('op', (e) => {
    // A journal fault must never break the reviewer's edit: the state has
    // already changed and 'change' must still fire after this listener.
    // The error's TYPE only: its message could carry a cell value.
    try { journal.record(e); } catch (err) { console.error('review journal:', err?.name); }
    scheduleFlush(500);
  });
  // pagehide, not beforeunload: it also fires on mobile tab discards.
  window.addEventListener('pagehide', () => {
    if (journal.active) beaconEvents(journal.sessionId, journal.writer, journal.pending);
  });
}

function scheduleFlush(delay) {
  clearTimeout(flushTimer);
  flushTimer = setTimeout(async () => {
    let ok = true;
    let stale = false;
    try {
      await journal.flush();
    } catch (err) {
      ok = false;
      // Another tab opened this drawing and holds the writer now; every
      // retry would be refused the same way (B3 replaces this with a
      // read-only banner).
      stale = /opened elsewhere/.test(err?.message || '');
    }
    switch (afterFlush({ ok, stale, pending: journal.pending.length, pendingOpen, finishing })) {
      case 'open':  flushBackoff = 500; loadForReview(pendingOpen); break;
      case 'open_stale':
        toast({ kind: 'warn', title: 'Opened in another tab',
                msg: 'This tab can no longer save its last changes to this drawing. Opening the waiting drawing.' });
        loadForReview(pendingOpen, { skipFlush: true });
        break;
      case 'flush':
        // Edits keep landing while a drawing waits: back off, or this would
        // post every 500 ms for as long as the reviewer keeps working.
        flushBackoff = Math.min(flushBackoff * 2, 10000);
        scheduleFlush(flushBackoff);
        break;
      case 'stale':
        // Said once per drawing, not again on every edit's flush.
        if (!staleShown) {
          staleShown = true;
          toast({ kind: 'warn', title: 'Opened in another tab', msg: 'Reload to continue here.' });
        }
        break;
      case 'retry':
        // Keep everything pending and retry, backing off to 10 s; Finish
        // refuses until the server holds every event, so nothing is lost.
        flushBackoff = Math.min(flushBackoff * 2, 10000);
        scheduleFlush(flushBackoff);
        break;
      default:   // idle: the next edit schedules the next flush
        if (ok) flushBackoff = 500;
        break;
    }
  }, delay);
}

// ===== Finish & Export ==============================================
function wireFinish() {
  const btn = document.getElementById('finish-btn');
  on('change', () => {
    if (!state.sessionId) return;
    const p = progress();
    btn.classList.toggle('blocked', !canFinish());
    btn.title = canFinish()
      ? 'Seal the review and export Excel + ballooned PDF'
      : `${p.outstanding} flagged row${p.outstanding === 1 ? '' : 's'} still to resolve`;
  });
  btn.addEventListener('click', finish);
}

async function finish() {
  const btn = document.getElementById('finish-btn');
  // A copy, so the sealed rows and the exported files describe one moment
  // even if an op lands while the run is awaiting the server.
  const snapshot = {
    sessionId: state.sessionId,
    rows: structuredClone(state.rows),
    reviewedIds: state.rows.filter((r) => r.reviewed).map((r) => r.id),
    notes: state.notes, marks: state.marks, title_block: state.title_block,
  };
  clearTimeout(flushTimer);   // the run flushes itself
  btn.disabled = true;        // no second click while sealing/exporting
  setBusy('Finishing…');
  finishing = true;
  try {
    const res = await finisher.run(snapshot);
    if (res.status === 'blocked') {
      const n = progress().outstanding;
      toast({ kind: 'warn', title: 'Not finished yet',
              msg: `${n} flagged row${n === 1 ? '' : 's'} still to resolve — showing them now.` });
      document.querySelector('#filter-pills button[data-filter="review"]')?.click();
    } else if (res.status === 'done') {
      toast({ kind: 'ok', title: 'Finished', msg: `Review sealed as revision r${res.revision}` });
    }
  } catch (err) {
    toast({ kind: 'error', title: 'Finish failed', msg: String(err.message || err) });
  } finally {
    finishing = false;
    setIdle();
    btn.disabled = !state.sessionId;
    // The flush timer was cleared above; if events are still pending (a
    // failed flush while offline, or a blocked run), restart the backoff loop
    // so they don't wait for the next edit -- and keep the loop alive while
    // a drawing waits to open behind this one (it opens once drained).
    if (journal.active && (journal.pending.length || pendingOpen)) scheduleFlush(flushBackoff);
  }
}

// ===== Undo / Redo ==================================================
function wireUndoRedo() {
  const u = document.getElementById('undo-btn');
  const r = document.getElementById('redo-btn');
  u.addEventListener('click', undo);
  r.addEventListener('click', redo);
  on('history', () => {
    u.disabled = !canUndo();
    r.disabled = !canRedo();
  });
}

// ===== Health =======================================================
async function pingHealth() {
  const pill  = document.getElementById('ocr-pill');
  const label = document.getElementById('ocr-label');
  const data  = await health();
  if (!data) {
    pill.classList.remove('ok'); pill.classList.add('warn');
    label.textContent = 'API unreachable';
    return;
  }
  pill.classList.add('ok');
  const backend = data.ocr_backend_active || data.backend || 'OCR';
  const short = String(backend).replace('Backend','');
  label.textContent = `OCR · ${short}`;
  pill.title = `OCR backend: ${backend}${data.cuda ? ' · CUDA' : ''}`;
}

document.addEventListener('DOMContentLoaded', init);
