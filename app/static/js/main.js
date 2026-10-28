// Entry point: wires modules, health check, file upload, the queue wait,
// reopening a drawing from its link, and Finish & Export.

import { state, on, restoreSession, clearSession, undo, redo,
         canUndo, canRedo, undoLabel, redoLabel, progress, canFinish, apply, opSetNote } from './state.js';
import { approvedCount, finishCopy, isCampaignDoc, noteToCommit } from './campaign.js';
import { getDocument, cancelDocument, openDocument, deleteDocument, downloadExport,
         health, postEvents, beaconEvents, sealSession, createBatch, getBatch,
         startBatch, discardBatch, setIncluded, listDocuments, moveToFront,
         reextractDocument, retryDocument, pauseQueue, resumeQueue, deleteUpload } from './api.js';
import { uploadFile, UploadError, forgetDraft, forgetDraftsOf, importFiles,
         isImportable, batchNameFor } from './upload.js';
import { importSingle, stagesOnly } from './importone.js';
import { badgesFor, workListBadges } from './badges.js';
import { createJournal } from './journal.js';
import { createFinisher, createExporter } from './finish.js';
import { restoreReview, flushBeforeLeaving, afterFlush, shouldReopen, openFailureCopy, unopenableDocCopy } from './reopen.js';
import { nextStopStep } from './stop.js';
import { initViewer } from './viewer.js';
import { initTable } from './table.js';
import { initWorkList } from './worklist.js';
import { parseRoute, docUrl, stagingUrl, worklistUrl, navigate } from './router.js';
import { prevId, nextAfterFinish, jumpCandidates, skip, navOrder, nextReadyCopy, filterJump } from './reviewbar.js';
import { initStagingView } from './stagingview.js';
import { createSerialRunner } from './serial.js';
import {
  toast, initModal, openHelp, initSplitter,
  initThemeAndDensity, initDragDrop,
} from './ui.js';
import { initShortcuts } from './shortcuts.js';
import { settleReasonPrompt } from './reasons.js';
import { autosize } from './autosize.js';
import { installAuthGate, submitToken } from './auth.js';

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
// A ready drawing the reviewer chose NOT to switch to yet, because the open
// one's changes had not saved. It opens by itself once they have
// (afterFlush); until then it is the one drawing this tab is waiting on.
let pendingOpen = null;   // id
let closePendingToast = null;
let workList = null;      // initWorkList()'s handle; Task 13 reads it back via workList.getDocs()
let routedDoc = null;     // { id, campaign } of the last GET /api/documents/{id} routed (currentDocIsCampaign)
let noteRefit = null;     // autosize()'s refit for #doc-note: call after setting its value or unhiding it
let noteSid = null;       // the drawing open when #doc-note took focus: its text belongs to that one

// Journal the note box's text onto the drawing it was typed on, if it
// changed (campaign.js noteToCommit). Blur alone is not enough: Back/Forward
// keeps the box focused, a reload or close never blurs it, and Finish's
// snapshot must already hold it. So every place that leaves or seals a
// drawing calls this first, while state.sessionId is still that drawing.
// apply() records into journal.pending synchronously, which is what lets
// pagehide's beacon carry it.
function commitNote() {
  const note = document.getElementById('doc-note');
  if (!note) return;
  const text = noteToCommit(noteSid, state.sessionId, note.value, state.note);
  if (text !== null) apply(opSetNote(text));
}
let stagingView = null;   // initStagingView()'s handle (this task)
// A single-drawing import's id, once started, while it is still on its way
// (design §3 "batch of one"): if the reviewer stays on the work list and
// opens nothing else meanwhile, it opens itself the moment it is ready --
// the same auto-open the old single-screen UI gave for free by never
// leaving the drawing's own screen. null once opened, cancelled, or the
// reviewer has moved on.
let autoOpenCandidate = null;
let autoOpenTimer = null;
// Every id this TAB has sealed with Finish this session, and not yet
// confirmed gone from the ready/in_review cache. Defense in depth for the
// browser-drive finding: right after Finish, the client's own 3 s poll
// window can still list the just-finished drawing as ready/in_review (the
// server has moved it on; the cache has not caught up), so skip()'s
// cross-batch fallback could otherwise pick it straight back. Pruned
// lazily in worklistDocsCache() -- "cleared on a refresh that confirms
// the state" means confirmed by whatever the cache says the NEXT time
// anything reads it, not by a callback wired into worklist.js's own poll.
let recentlyFinished = new Set();

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
  // A campaign drawing is approved, never exported (campaign design §3):
  // the seal is the whole act, and the server refuses its export anyway.
  // Ordinary drawings keep both downloads.
  exportAll: (() => {
    const exporter = createExporter((sid, kind, revision) => downloadExport(sid, kind, revision));
    // Asked of the SNAPSHOT's drawing, not of whatever is open by the time
    // the seal returns.
    return (snapshot, revision) => (docIsCampaign(snapshot.sessionId) ? Promise.resolve() : exporter(snapshot, revision));
  })(),
});

// Optional shared access token (batch design §1, decision 23). Installed at
// module load, before DOMContentLoaded/init() ever runs a fetch, so the very
// first request this tab makes (pingHealth, or a resumed #/doc link) is
// already covered -- there is no window where an early 401 slips past the
// gate because it fired before installAuthGate() ran. The token itself is
// never read back out of the DOM except as the FormData value handed
// straight to submitToken -- never logged, stored, or put in a URL.
function promptForToken() {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'token-prompt-overlay';
    overlay.innerHTML = `
      <form class="token-prompt">
        <label>Access token<input type="password" name="token" autocomplete="current-password" autofocus></label>
        <button type="submit">Continue</button>
        <p class="token-prompt-error" hidden>Incorrect token.</p>
      </form>`;
    document.body.appendChild(overlay);
    const form = overlay.querySelector('form');
    const input = overlay.querySelector('input[name="token"]');
    const error = overlay.querySelector('.token-prompt-error');
    input.focus();
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      error.hidden = true;
      const token = new FormData(form).get('token');
      const ok = await submitToken(token);
      if (ok) {
        overlay.remove();
        resolve(true);
      } else {
        error.hidden = false;
        input.value = '';
        input.focus();
      }
    });
  });
}

installAuthGate(promptForToken);

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
  // The reviewer's per-drawing note (campaign design §3), journaled as
  // set_note on blur rather than per keystroke: the server keeps only the
  // latest net text anyway, and one event per pause is all grading reads.
  // Focus records WHICH drawing the text is being typed for (commitNote).
  const note = document.getElementById('doc-note');
  note?.addEventListener('focus', () => { noteSid = state.sessionId; });
  note?.addEventListener('blur', commitNote);
  // Grows with its text; refit after every programmatic value change
  // (renderFinish below) since no input event fires for those.
  noteRefit = note ? autosize(note) : null;
  wireJournal();
  wireUndoRedo();
  pingHealth();
  workList = initWorkList({
    listDocuments, moveToFront, cancelDocument, retryDocument, deleteDocument,
    reextractDocument, pauseQueue, resumeQueue, toast,
    badgesFor: workListBadges,
  });
  stagingView = initStagingView({
    getBatch, setIncluded, startBatch, discardBatch, deleteDocument,
    deleteUpload, importFiles, isImportable, toast,
    onStarted: (res) => {
      // res is startBatch's { queued, dropped } (repo.py's start_batch),
      // or { discarded: true } from the Discard button -- only a genuine
      // start of exactly one drawing is a "batch of one" to auto-open.
      if (res && Array.isArray(res.queued) && res.queued.length === 1) {
        autoOpenCandidate = res.queued[0];
        watchAutoOpen();
      }
      go(worklistUrl());
    },
  });
  wireImport();
  wireReviewBar();
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') { e.preventDefault(); openJumpPalette(); }
  });
  window.addEventListener('hashchange', routeFromHash);
  routeFromHash();   // replaces the old resumeFromHash() call
}

// ===== Router =========================================================
// The hash scheme moved from B1's `#doc=<id>` to router.js's `#/`,
// `#/staging/<batch>`, `#/doc/<id>` (decision 3). setHash now takes a
// whole URL (one of router.js's builders) rather than a bare id.
function setHash(url) { history.replaceState(null, '', url); }
function currentRoute() { return parseRoute(location.hash); }

// history.replaceState (setHash above) never fires `hashchange` -- that
// event is the BROWSER's own notice of a navigation it performed, and a
// script writing history silently is invisible to it (decision 10). Every
// router-driven entry point (Prev, Skip, Jump, Finish's chain-to-next, the
// post-import and post-start redirects) calls go(), not setHash(), so the
// screen actually updates instead of only the address bar. The handful of
// EXISTING setHash() calls elsewhere in this file (showConfirm's caller, the
// extracting overlay, a freshly opened review) already update their own
// screen through a direct function call and stay as plain setHash() --
// re-dispatching for those would refetch a document already on screen.
function go(url) {
  navigate(url, {
    setUrl: (u) => history.replaceState(null, '', u),
    dispatch: () => { routeFromHash(); },
  });
}

// Re-entrancy guard lives HERE, not inside go() -- routeFromHash also runs
// from the native `hashchange` listener (a real link click, back/forward),
// which go()'s own dispatch does not see. Guarding only go() would still
// let a real link click race a go()-driven dispatch still in flight; this
// way every path that can start a route dispatch shares the same guard.
// createSerialRunner (serial.js, node-tested) QUEUES rather than drops a
// navigation that arrives mid-route (EXECUTION REQUIREMENT, plan
// re-review): a second hashchange/go() call while a route is already in
// flight would otherwise be silently lost -- e.g. two Skips in quick
// succession must land on the SECOND target, not get stuck on whichever
// hash happened to be current when the first dispatch started. routeOnce
// takes no argument and reads currentRoute() itself, so the re-run the
// guard triggers sees whatever hash is current AT THAT MOMENT, not a
// snapshot taken when the extra call arrived.
const router = createSerialRunner(routeOnce);
function routeFromHash() { return router.run(); }

async function routeOnce() {
  // An open reason card no longer holds any op back (reasons.js applies at
  // commit), so this only closes it: the card must not follow the reviewer to
  // the next drawing, and a late pick must not swap an op in a journal that
  // is about to be resumed or sealed. go() writes the hash with replaceState,
  // which fires no hashchange (decision 10), so the card's own listener would
  // never hear about it.
  await settleReasonPrompt();
  // Same reason for an un-blurred note: state.sessionId is still the drawing
  // being left, which is the one its text belongs to.
  commitNote();
  const route = currentRoute();
  // A Finish in flight is mid-seal: finisher.run() reads journal.writer at
  // the top and again when it posts the seal, and opening ANOTHER drawing
  // in between rotates that writer out from under it (openForReview ->
  // openDocument -> journal.resume()), so the seal the server receives no
  // longer matches the writer Finish started with. Blocked centrally here
  // rather than only at each button, so a direct hash edit or a stale
  // go() queued just before Finish started cannot slip past the buttons'
  // own guard (warnFinishing, wireReviewBar). The hash already changed by
  // the time this runs (decision 10's "stay must put it back itself"), so
  // revert it to the drawing actually being finished.
  if (finishing && route.name === 'doc') {
    warnFinishing();
    setHash(docUrl(journal.sessionId));
    return;
  }
  // Leaving a drawing (to the work list or staging) must not silently drop
  // unsaved review edits -- the doc-to-doc case (Prev/Skip/Jump/Finish's
  // chain, or a hash edited by hand to another #/doc/<id>) is already
  // guarded inside openForReview, reached via routeDocument below, so this
  // only needs to cover navigation AWAY from review entirely. The hash (or
  // go()'s setUrl) has already written the new URL by the time this runs --
  // native hashchange fires after the browser's own navigation, and go()
  // calls setUrl before dispatch -- so "stay" must put it back itself.
  if (route.name !== 'doc' && journal.active) {
    if (!(await flushBeforeLeaving(journal, confirmLeaveUnsaved))) {
      setHash(docUrl(journal.sessionId));
      return;
    }
  }
  showScreen(route.name);
  if (route.name === 'doc') {
    resuming = true;   // set before the first await: an upload now would race the link
    try {
      const doc = await getDocument(route.id);
      await routeDocument(doc);
    } catch (err) {
      // Only a drawing that is really gone loses its link; a hiccup keeps it
      // so a reload tries again.
      if (err?.status === 404) {
        setHash(worklistUrl());
        toast({ kind: 'warn', title: 'Drawing not found', msg: 'The drawing in this link was removed.' });
        showScreen('worklist');
        // A deletion is invisible to the since-cursor poll (decision 11):
        // the work list's cached copy of this (now-gone) drawing would
        // otherwise sit there until its next scheduled full reload, up to
        // ~1 minute away. A 404 on a drawing it had cached is the clearest
        // possible signal that its cache is stale right now.
        workList?.refresh(true);
      } else {
        toast({ kind: 'error', title: 'Could not reach the drawing',
                msg: `${String(err?.message || err)} — reload the page to try again.` });
      }
    } finally {
      resuming = false;
    }
  } else if (route.name === 'staging') {
    await stagingView.load(route.batchId);
  }
}

function showScreen(name) {
  document.getElementById('screen-worklist').hidden = name !== 'worklist';
  document.getElementById('screen-staging').hidden = name !== 'staging';
  document.getElementById('shell-body').hidden = name !== 'doc';
  if (name !== 'doc') setReviewBarVisible(false);
}

// The review bar (batch label, Prev/Skip/Jump) only makes sense once a
// drawing is actually open FOR REVIEW. #/doc/<id> for a drawing that is
// still staged, queued, extracting, failed or cancelled shows the confirm
// panel or the extracting overlay instead (both already live inside
// shell-body, unchanged from B1) -- there is no batch position to report
// for those, so the bar stays hidden until routeDocument says otherwise.
function setReviewBarVisible(visible) {
  const el = document.getElementById('review-bar-nav');
  if (el) el.hidden = !visible;
  if (visible) updateReviewBarInfo();
}

// "Batch '<name>'" and "N of M" -- both server strings (file/batch names),
// so textContent only, never innerHTML. navOrder is the exact list
// Prev/Skip/Jump walk (decision 8), so the position always agrees with
// what those buttons would do.
function updateReviewBarInfo() {
  const docs = worklistDocsCache();
  const batchId = currentBatchId();
  const order = navOrder(docs, batchId);
  const idx = order.findIndex((d) => d.id === state.sessionId);
  const batchName = docs.find((d) => d.batch_id === batchId)?.batch_name || '…';
  const batchEl = document.getElementById('rb-batch');
  const posEl = document.getElementById('rb-position');
  if (batchEl) batchEl.textContent = `Batch "${batchName}"`;
  if (posEl) posEl.textContent = idx >= 0 ? `${idx + 1} of ${order.length}` : '';
  syncCampaignChrome();
}

// Approve vs Finish & Export, the note box and the "N / M approved" counter
// all follow ONE question -- is the open drawing a campaign drawing -- so
// they are set together, from every place that question's answer can change
// (a new session, any review change, the review bar being shown).
function syncCampaignChrome() {
  const campaign = currentDocIsCampaign();
  const copy = finishCopy(campaign);
  const label = document.getElementById('finish-label');
  if (label && label.textContent !== copy.label) label.textContent = copy.label;
  const btn = document.getElementById('finish-btn');
  // A blocked button's title names the outstanding rows (wireFinish); only
  // a finishable one carries the promise of what Finish/Approve does.
  if (btn && canFinish()) btn.title = copy.title;
  const note = document.getElementById('doc-note');
  if (note) {
    note.hidden = !campaign || !state.sessionId;
    // Never overwrite what the reviewer is typing -- unless the drawing
    // changed under a focused box (Back/Forward), when the text on screen
    // is the previous drawing's and must not be shown as this one's.
    // Drop focus rather than re-bind: restoreSession emits 'session'/'change'
    // with state.note '' BEFORE replaying the new drawing's set_note, so a
    // re-bound focused box would hold '' and its next blur erase that note.
    // This blur's commitNote() is a no-op (noteSid is still the old drawing).
    if (document.activeElement !== note || state.sessionId !== noteSid) {
      note.value = state.note || '';
      if (document.activeElement === note) note.blur();
    }
    noteRefit?.();   // after hidden/value changed: a hidden box measures 0
  }
  const appr = document.getElementById('rb-approved');
  if (appr) {
    const { approved, total } = campaign
      ? approvedCount(worklistDocsCache(), currentBatchId()) : { approved: 0, total: 0 };
    // total 0 = the batch is not in the work-list cache yet; "0 / 0" would lie.
    appr.hidden = !campaign || total === 0;
    if (!appr.hidden) appr.textContent = `${approved} / ${total} approved`;
  }
}

// Batch-of-one auto-open (design §3, decision 17 -- one drawing is still a
// batch of one). Self-contained rather than piggy-backing on the work
// list's own poll: it only needs to watch ONE id until it leaves the
// queue, and stops the moment its own two conditions (still on the work
// list, nothing else opened) stop holding -- cheaper to read on its own
// than to thread a callback through worklist.js for a single caller.
function watchAutoOpen() {
  clearTimeout(autoOpenTimer);
  if (!autoOpenCandidate) return;
  autoOpenTimer = setTimeout(async () => {
    if (!autoOpenCandidate) return;
    if (currentRoute().name !== 'worklist' || state.sessionId) {
      autoOpenCandidate = null;   // the reviewer moved on; its own link still opens it later
      return;
    }
    let doc;
    try {
      doc = await getDocument(autoOpenCandidate);
    } catch {
      autoOpenCandidate = null;   // gone, or unreachable -- nothing worth retrying forever for
      return;
    }
    if (!autoOpenCandidate) return;   // cancelled while the request was out
    if (doc.state === 'ready' || doc.state === 'in_review') {
      // Re-checked here, not only before the await above: the reviewer can
      // navigate away from the work list, or open a different drawing,
      // during the getDocument() round trip -- the condition must hold at
      // the moment of go(), not merely when this tick started.
      if (currentRoute().name !== 'worklist' || state.sessionId) {
        autoOpenCandidate = null;
        return;
      }
      const id = autoOpenCandidate;
      autoOpenCandidate = null;
      go(docUrl(id));
      return;
    }
    if (doc.state !== 'queued' && doc.state !== 'extracting') {
      autoOpenCandidate = null;   // failed, cancelled, or otherwise done waiting for
      return;
    }
    watchAutoOpen();
  }, 3000);
}

async function routeDocument(doc) {
  routedDoc = { id: doc.id, campaign: doc.campaign ?? null };
  waitingOn = { id: doc.id, batchId: doc.batch_id, fileName: doc.file_name, pages: doc.pages };
  if (doc.state === 'staged') {
    setReviewBarVisible(false);
    await showStaged(doc);
  } else if (doc.state === 'queued' || doc.state === 'extracting') {
    setReviewBarVisible(false);
    showExtracting(doc.file_name);
    pollUntilReady();
  } else if (['ready', 'in_review', 'finished'].includes(doc.state)) {
    await loadForReview(doc.id);
    setReviewBarVisible(true);
  } else {
    // failed, cancelled, or purged -- none of these are worth a POST /open:
    // a purged drawing in particular has no rows left to review, so routing
    // it through loadForReview would show the review bar just to collect
    // the 410 openFailureCopy already knows how to word. unopenableDocCopy
    // shares that exact retention sentence instead of a second copy of it.
    setReviewBarVisible(false);
    const { title, msg } = unopenableDocCopy(doc);
    toast({ kind: 'error', title, msg });
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
// dropped while reviewing another), else the router's home screen.
function backToEmptyOrCurrent() {
  setHash(state.sessionId ? docUrl(state.sessionId) : worklistUrl());
  if (state.sessionId) {
    document.getElementById('plan-confirm').hidden = true;
    document.getElementById('plan-extracting').hidden = true;
  } else {
    // "plan-empty"'s drop zone was B1's single-screen home; the router's
    // home screen is the work list now (decision 9), so this branch shows
    // that instead. The markup itself stays in index.html -- it is NOT
    // dead: viewer.js still shows/hides it as the drawing viewport's own
    // "no image loaded" placeholder, independent of this router.
    showScreen('worklist');
  }
}

// ===== Header / file chip ============================================
function wireHeader() {
  document.getElementById('file-close').addEventListener('click', async () => {
    // The beacon below sends journal.pending and clearSession() then ends
    // the drawing: the reason card must not outlive it (ops are applied at
    // commit now, so nothing is held back by it).
    await settleReasonPrompt();
    commitNote();   // likewise an un-blurred note, before the beacon below
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
    hideStaleBanner();
    hideLaggingBanner();
    setHash(worklistUrl());
    // A drawing was waiting for this one's changes to save. Closing is the
    // reviewer leaving anyway (the beacon above was the last try), so open
    // it now rather than leave it waiting on a drawing nobody is reviewing.
    if (pendingOpen) loadForReview(pendingOpen, { skipFlush: true });
    else showScreen('worklist');   // decision 9: the router's home is the work list, not an empty review
  });
  on('session', () => {
    const chip  = document.getElementById('file-chip');
    const name  = document.getElementById('file-name');
    if (state.sessionId) {
      chip.hidden = false;
      name.textContent = state.fileName || 'drawing.pdf';
      document.getElementById('finish-btn').disabled = false;
      syncCampaignChrome();
    } else {
      chip.hidden = true;
      const fin = document.getElementById('finish-btn');
      fin.disabled = true;
      // The 'change' handler skips a cleared session, so reset here or the
      // next drawing opens with the last one's "N flagged rows" state.
      fin.classList.remove('blocked');
      fin.title = finishCopy(false).title;
      // Hides the note box and counter and puts the ordinary label back, so
      // a closed campaign drawing leaves no Approve chrome behind.
      syncCampaignChrome();
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
  // Drag-and-drop listens on the whole window, so no [data-campaign-hide]
  // can remove it; say plainly why nothing happens instead of uploading
  // into a guaranteed 403 (plan C1 decision 10).
  if (document.body.classList.contains('campaign-only')) {
    toast({ kind: 'warn', title: 'Uploads are off here', msg: 'This instance is for reviewing the drawings already in the list.' });
    return;
  }
  // Before /api/health answers we cannot know whether uploads are off.
  if (document.body.classList.contains('health-pending')) {
    toast({ kind: 'warn', title: 'Still connecting to the server', msg: 'Try again in a moment.' });
    return;
  }
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
    setHash(docUrl(row.id));
    // Unlike the router-driven entry points, a drag-drop/picker upload can
    // land while the work list (or staging) screen is showing -- showConfirm
    // only fills shell-body's markup, so the screen itself must be switched
    // here or the confirm panel renders invisibly behind the current screen.
    showScreen('doc');
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

// ===== Work list: the `+ Import` entry points ========================
// Two pickers feed the same path: plain files / a ZIP (`wl-import-input`,
// `accept=".pdf,.zip"` already refuses the wrong type at the OS dialog) and
// a whole folder (`wl-import-folder-input`, `webkitdirectory` -- design §2
// requires importing a folder, not only loose files or a ZIP). A folder
// picker has no `accept` filter at all (the attribute is ignored with
// `webkitdirectory`), so its files are screened with `isImportable` here;
// whatever survives carries its own `webkitRelativePath`, which
// `uploadFile`'s `relPath` default (`relPathOf`, upload.js) reads without
// this file doing anything extra.
function wireImport() {
  document.getElementById('wl-import')?.addEventListener('click', () => {
    document.getElementById('wl-import-input')?.click();
  });
  document.getElementById('wl-import-input')?.addEventListener('change', (e) => startImport(e, false));
  document.getElementById('wl-import-folder')?.addEventListener('click', () => {
    document.getElementById('wl-import-folder-input')?.click();
  });
  document.getElementById('wl-import-folder-input')?.addEventListener('change', (e) => startImport(e, true));
}

async function startImport(e, isFolder) {
  const picked = [...e.target.files];
  e.target.value = '';   // a picker holding the same files fires no 'change' on a re-pick otherwise
  if (!picked.length) return;
  // The plain-files picker's `accept` already keeps non-PDF/ZIP files out
  // of the dialog; a folder picker has no such filter, so its contents are
  // screened the same way the server would judge them (isImportable).
  const files = isFolder ? picked.filter((f) => isImportable(f.name)) : picked;
  const skippedByType = picked.length - files.length;
  if (!files.length) {
    toast({ kind: 'warn', title: 'Nothing to import',
            msg: 'No .pdf or .zip files were found in that folder.' });
    return;
  }
  setBusy('Importing…');
  try {
    // batchNameFor (decision 27): a single ZIP's name, a single folder's
    // name, else the date and time -- one rule for both pickers, instead of
    // this file guessing a name from the picker it happened to be called from.
    const { id: batchId } = await createBatch(batchNameFor(files));
    const results = await importFiles(files, { batchId });
    const failed = results.filter((r) => !r.ok).length;
    if (failed || skippedByType) {
      // A disk-full (507) or any other fatal failure lands here too --
      // the staging view's own Discard button (Task 12) is the recovery
      // for a draft that can never complete, so this points at it rather
      // than retrying forever itself.
      const parts = [];
      if (failed) parts.push(`${failed} of ${files.length} failed`);
      if (skippedByType) parts.push(`${skippedByType} skipped (not .pdf/.zip)`);
      toast({ kind: 'warn', title: 'Some files did not import',
              msg: `${parts.join('; ')}. Open the import to retry or discard it.` });
    }
    // go(), not setHash(): the staging screen must actually appear, and
    // history.replaceState alone never fires hashchange (decision 10).
    go(stagingUrl(batchId));
  } catch (err) {
    toast({ kind: 'error', title: 'Could not start the import', msg: String(err?.message || err) });
  } finally {
    setIdle();
  }
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
          + `Close this; its link is ${location.origin}${location.pathname}${docUrl(id)}`;
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
  // A freshly opened drawing never keeps yesterday's stale banner: this tab
  // may be opening a NEW drawing while still shown read-only for the one it
  // just left (the read-only state is per screen, not per drawing).
  hideStaleBanner();
  hideLaggingBanner();
  // Defense-in-depth (the primary fix is jumpCandidates' excludeId, which
  // never OFFERS the open drawing as a target): a stale link, a hash
  // edited by hand, or a queued navigation could still ask to open the
  // drawing already open. A fresh /open would mint a new writer and
  // journal.resume() would wipe unflushed `pending` events for a drawing
  // that was never actually left -- so this is a pure no-op on the
  // network, just making sure the screen/hash/review bar agree with what
  // is already true.
  if (!shouldReopen(journal, id)) {
    if (waitingOn?.id === id) waitingOn = null;
    setHash(docUrl(id));
    showScreen('doc');
    setReviewBarVisible(true);
    return;
  }
  // Reached without routeOnce too (scheduleFlush's 'open'/'open_stale', the
  // file-close handler's pendingOpen), and journal.resume() below wipes
  // whatever is unflushed -- so the reason card, whose late pick would swap
  // an op in that journal, is closed before anything decides to leave.
  // Below the no-op return above: re-asserting the drawing already open
  // must not dismiss a card the reviewer is still reading.
  await settleReasonPrompt();
  // Taken before any await that yields to a TASK (settleReasonPrompt above
  // is microtask-only, so no flush tick can run in between), so a second
  // flush tick cannot start a second open of the same waiting drawing.
  // "Stay" re-sets it.
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
           + `Its link: ${location.origin}${location.pathname}${docUrl(id)}` });
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
    if (waitingOn?.id === id) waitingOn = null;   // never another drawing's
    setHash(docUrl(id));
    // openForReview is reached by more than one caller's dispatch --
    // routeDocument's own #/doc/<id> route, but also a flush completing in
    // the background (scheduleFlush's 'open'/'open_stale') and the
    // file-close handler opening a drawing that was waiting (pendingOpen).
    // None of those go through routeDocument's own showScreen/
    // setReviewBarVisible calls, so a successful open asserts them itself
    // rather than leaving the screen wherever it happened to be.
    showScreen('doc');
    setReviewBarVisible(true);
    hideExtracting();
    const n = data.rows.length;
    const nEv = data.events.length;
    toast({ kind: 'ok', title: `Loaded ${data.fileName}`,
            msg: nEv ? `Review restored (${nEv} change${nEv === 1 ? '' : 's'})`
                     : `${n} characteristic${n === 1 ? '' : 's'} extracted` });
  } catch (err) {
    if (waitingOn?.id === id) waitingOn = null;   // never another drawing's
    hideExtracting();
    // Carried from B4 Task 9 (decision 8): a 410 means retention already
    // deleted this drawing's files, permanently -- unlike every other
    // failure here (a transient 409, a 404, a network hiccup), which still
    // stays "try again" and keeps the link so a reload retries it. A
    // tombstone offers no retry at all and always routes back to the work
    // list, even if this tab had another drawing open before trying to
    // reach this one.
    const { purged, message } = openFailureCopy(err);
    if (purged) {
      // backToEmptyOrCurrent() alone is enough: unlike the generic branch
      // below, this never falls through to the "keep the dead link so a
      // reload retries it" case -- there is deliberately no retry for a
      // tombstone, so the hash either stays on another drawing this tab
      // still has open, or goes to the work list; never back to `id`.
      toast({ kind: 'error', title: 'This drawing is gone', msg: message });
      backToEmptyOrCurrent();
      return;
    }
    toast({ kind: 'error', title: 'Could not open the drawing',
            msg: state.sessionId && state.sessionId !== id
              ? `${String(err.message || err)} — its link: ${location.origin}${location.pathname}${docUrl(id)}`
              : `${String(err.message || err)} — reload the page to try again.` });
    if (state.sessionId) {
      backToEmptyOrCurrent();
    } else {
      // Keep the link on the drawing that failed, so a reload retries it --
      // the router's home screen (decision 9) is the work list, not B1's
      // single-screen empty state.
      showScreen('worklist');
      setHash(docUrl(id));
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
    // A reload or close never blurs the note box; committing it here puts
    // its set_note in journal.pending before the first beacon reads it.
    commitNote();
    if (!journal.active) return;
    const { sessionId, writer } = journal;
    const sent = journal.pending.length;
    beaconEvents(sessionId, writer, journal.pending);
    // A pagehide listener cannot await. Beaconing first keeps the common
    // case unchanged; the settleReasonPrompt() call below closes any open
    // card, and the `.then` re-beacons if anything was journaled meanwhile.
    // Append is by seq server-side, so the overlap double-sends at worst,
    // never drops.
    settleReasonPrompt().then(() => {
      if (journal.pending.length > sent) beaconEvents(sessionId, writer, journal.pending);
    });
  });
  document.getElementById('banner-lagging-retry')?.addEventListener('click', () => scheduleFlush(0));
}

// The losing side of a two-tab takeover (StaleWriter; design §3): persistent,
// not a toast that scrolls away, because the refusal is permanent for this
// tab -- only a reload gets a fresh writer. state.readOnly makes it actually
// block edits (apply()/undo()/redo() in state.js), not merely look blocked.
function showStaleBanner(unsavedCount) {
  state.readOnly = true;
  const el = document.getElementById('banner-stale');
  if (!el) return;
  el.hidden = false;
  const countEl = document.getElementById('banner-stale-count');
  if (countEl) countEl.textContent = unsavedCount ? ` (${unsavedCount} change${unsavedCount === 1 ? '' : 's'} not kept)` : '';
  // Read-only now: further edits would only be refused the same way, so
  // disable the controls that would otherwise look live. state.readOnly
  // already refuses the mutation itself; this adds the visible cue the
  // design asks for on top of that guard.
  document.getElementById('finish-btn').disabled = true;
}

function hideStaleBanner() {
  state.readOnly = false;
  const el = document.getElementById('banner-stale');
  if (el) el.hidden = true;
}

// The journal's own retry/backoff (journal.js) was silent before except for
// whatever toast happened to be on screen; this makes the 'retry' state
// visible with a live countdown to the next scheduled flush, plus a manual
// retry button that jumps the backoff.
let laggingTimer = null;

function showLaggingBanner(retryAt) {
  const el = document.getElementById('banner-lagging');
  if (!el) return;
  el.hidden = false;
  clearInterval(laggingTimer);
  const tick = () => {
    const el2 = document.getElementById('banner-lagging-countdown');
    if (el2) el2.textContent = String(Math.max(0, Math.ceil((retryAt - Date.now()) / 1000)));
  };
  tick();
  laggingTimer = setInterval(tick, 250);
}

function hideLaggingBanner() {
  clearInterval(laggingTimer);
  const el = document.getElementById('banner-lagging');
  if (el) el.hidden = true;
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
        // showStaleBanner's own `el.hidden` check makes a repeat call here a
        // no-op visually, so no "already shown" flag is needed any more.
        showStaleBanner(journal.pending.length);
        break;
      case 'retry':
        // Keep everything pending and retry, backing off to 10 s; Finish
        // refuses until the server holds every event, so nothing is lost.
        flushBackoff = Math.min(flushBackoff * 2, 10000);
        showLaggingBanner(Date.now() + flushBackoff);
        scheduleFlush(flushBackoff);
        break;
      default:   // idle: the next edit schedules the next flush
        if (ok) { flushBackoff = 500; hideLaggingBanner(); }
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
      ? finishCopy(currentDocIsCampaign()).title
      : `${p.outstanding} flagged row${p.outstanding === 1 ? '' : 's'} still to resolve`;
    syncCampaignChrome();
  });
  btn.addEventListener('click', finish);
}

async function finish() {
  // Before the snapshot: close the reason card so a late pick cannot swap an
  // op after the revision was copied. Only microtasks pass here, so a second
  // click (its own task) cannot slip in before `finishing` and btn.disabled
  // are set below.
  await settleReasonPrompt();
  commitNote();   // a note still being typed is part of what is sealed
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
  setReviewBarButtonsDisabled(true);
  try {
    const res = await finisher.run(snapshot);
    // Cleared the moment finisher.run() resolves, not only in the `finally`
    // below: the chain-to-next go() calls a few lines down are themselves
    // navigation, and routeOnce's own finishing guard would otherwise block
    // Finish's OWN successful chain to the next drawing. The race this
    // guard exists for (another navigation rotating journal.writer while
    // finisher.run() is reading it) is over the instant run() returns.
    finishing = false;
    setReviewBarButtonsDisabled(false);
    if (res.status === 'blocked') {
      const n = progress().outstanding;
      toast({ kind: 'warn', title: 'Not finished yet',
              msg: `${n} flagged row${n === 1 ? '' : 's'} still to resolve — showing them now.` });
      document.querySelector('#filter-pills button[data-filter="review"]')?.click();
    } else if (res.status === 'done') {
      // Read now: go() below moves state.sessionId to the next drawing.
      const campaign = currentDocIsCampaign();
      toast({ kind: 'ok', title: campaign ? 'Approved' : 'Finished',
              msg: `Review sealed as revision r${res.revision}` });
      // The browser drive's finding: the work list's own 3 s poll window
      // can still list THIS drawing as ready/in_review right here -- the
      // server has just sealed it, but nothing has told the client cache
      // yet. Mark it locally for an instant correct read (markDoc,
      // worklist.js) and keep it in `recentlyFinished` as a defense-in-depth
      // belt (reviewbar.js composes it into every exclusion below), THEN
      // await an authoritative refresh before computing where to go next --
      // nextAfterFinish must never be able to pick the drawing just sealed.
      recentlyFinished.add(snapshot.sessionId);
      workList?.markDoc(snapshot.sessionId, { state: 'finished' });
      await workList?.refresh(true);
      // The counter reads the cache just refreshed; nothing else re-syncs
      // it if the chain below stays on this drawing (extracting / done).
      syncCampaignChrome();
      // finisher.run() returns only { status, revision } (finish.js) -- the
      // snapshot taken at the top of finish() is what names which drawing
      // this run was for. currentBatchId() still reads state.sessionId,
      // which has not moved on yet at this point in the function.
      const next = nextAfterFinish(worklistDocsCache(), currentBatchId(), snapshot.sessionId, recentlyFinished);
      if (next.status === 'next') {
        go(docUrl(next.id));
      } else if (next.status === 'extracting') {
        // nextAfterFinish's 'extracting' branch only knows SOMETHING is on
        // its way, not which drawing -- but the work list's own queue
        // summary (the SAME one its header already shows, via getQueue())
        // does carry a per-drawing estimate once nextReadyCopy divides its
        // cumulative eta_seconds by how many are queued (CLAUDE.md §4: a
        // number with no basis is "estimating…", never a guess).
        const copy = nextReadyCopy(workList?.getQueue());
        toast({ kind: 'warn', title: copy.title, msg: copy.msg });
      } else {
        // B4 builds mass export; until then the work list's per-drawing
        // export buttons (already shipped) are what "Export batch" means.
        // A campaign has nothing to export (campaign design §3), so its end
        // says what the reviewer can still do instead.
        toast({ kind: 'ok',
                title: campaign ? 'All drawings approved — thank you' : 'Batch complete',
                msg: campaign ? 'You can still reopen any drawing from the list.'
                              : 'Export batch from the work list.' });
        go(worklistUrl());
      }
    }
  } catch (err) {
    toast({ kind: 'error', title: 'Finish failed', msg: String(err.message || err) });
  } finally {
    finishing = false;
    setReviewBarButtonsDisabled(false);
    setIdle();
    btn.disabled = !state.sessionId;
    // The flush timer was cleared above; if events are still pending (a
    // failed flush while offline, or a blocked run), restart the backoff loop
    // so they don't wait for the next edit -- and keep the loop alive while
    // a drawing waits to open behind this one (it opens once drained).
    if (journal.active && (journal.pending.length || pendingOpen)) scheduleFlush(flushBackoff);
  }
}

// ===== Review bar ====================================================
function wireReviewBar() {
  document.getElementById('rb-prev')?.addEventListener('click', () => {
    if (warnFinishing()) return;
    // recentlyFinished: defense in depth so Prev cannot land on a drawing
    // this tab just sealed, even if the cache has not caught up yet.
    const id = prevId(worklistDocsCache(), currentBatchId(), state.sessionId, recentlyFinished);
    if (id) go(docUrl(id));
  });
  document.getElementById('rb-skip')?.addEventListener('click', () => {
    if (warnFinishing()) return;
    // skip() calls `go` itself (reviewbar.js) -- it takes `navigate` as a
    // dependency rather than returning an id for the caller to setHash()
    // with, which is exactly the shape of mistake decision 10 is about.
    skip(worklistDocsCache(), currentBatchId(), state.sessionId, { navigate: go, docUrl, excludeIds: recentlyFinished });
  });
  document.getElementById('rb-jump')?.addEventListener('click', openJumpPalette);   // guards itself (warnFinishing)
}

// Prev/Skip/Jump (buttons, Ctrl+K, and the palette's own Enter/click) all
// end in a call to go() that would open another drawing mid-Finish --
// finisher.run() is reading journal.writer across its own await boundary
// (the flush, then the seal), and opening a different drawing rotates that
// writer out from under it (openForReview -> journal.resume()). This is
// the guard they all share; routeOnce has the same check centrally, for
// the paths these buttons do not cover (a direct hash edit, a queued
// go()). Returns true when it blocked the action, so a caller can `return`.
function warnFinishing() {
  if (!finishing) return false;
  toast({ kind: 'warn', title: 'Finishing — one moment', msg: 'This drawing is being sealed. Try again once it finishes.' });
  return true;
}

// Visually matches warnFinishing's refusal: a disabled button not even
// clickable is clearer than one that clicks and immediately toasts.
function setReviewBarButtonsDisabled(disabled) {
  const prev = document.getElementById('rb-prev');
  const skipBtn = document.getElementById('rb-skip');
  const jump = document.getElementById('rb-jump');
  if (prev) prev.disabled = disabled;
  if (skipBtn) skipBtn.disabled = disabled;
  if (jump) jump.disabled = disabled;
}

function currentBatchId() {
  return worklistDocsCache().find((d) => d.id === state.sessionId)?.batch_id || null;
}

// The work-list row is the source of truth (same list as the review bar);
// routeDocument's own GET is the fallback for a #/doc link opened before
// the work list's first poll has landed -- without it that drawing would
// offer "Finish & Export" and try an export the server refuses.
function docIsCampaign(id) {
  if (!id) return false;
  const cached = worklistDocsCache().find((d) => d.id === id);
  if (cached) return isCampaignDoc(cached);
  return routedDoc?.id === id && isCampaignDoc(routedDoc);
}

function currentDocIsCampaign() {
  return docIsCampaign(state.sessionId);
}

// A minimal in-page combobox: a text input filtered by filterJump
// (reviewbar.js, node-tested) plus a list, not a new design system. File
// names are server strings, so every list item is built via textContent,
// never innerHTML. One instance's listeners are all removed on close, so
// pressing Ctrl+K again starts clean rather than stacking handlers.
function openJumpPalette() {
  // Covers Ctrl+K (init()'s keydown listener calls this directly, bypassing
  // wireReviewBar's own click guard) as well as the button's click, which
  // already checked but costs nothing to check again here.
  if (warnFinishing()) return;
  const backdrop = document.getElementById('jump-backdrop');
  const input = document.getElementById('jump-input');
  const list = document.getElementById('jump-list');
  if (!backdrop || !input || !list) return;
  if (!backdrop.hidden) return;   // already open -- Ctrl+K again is a no-op, not a second instance

  // Excludes the drawing already open (selecting it would re-/open it,
  // minting a new writer and wiping unflushed events -- reopen.js's
  // shouldReopen is the defense-in-depth half of that fix, for every path
  // that is not this palette) AND every id this tab has just sealed with
  // Finish, in case the cache has not caught up with the server yet.
  const candidates = jumpCandidates(worklistDocsCache(), [state.sessionId, ...recentlyFinished]);
  const previouslyFocused = document.activeElement;
  let filtered = candidates;
  let activeIndex = 0;

  function renderList() {
    list.innerHTML = '';
    filtered.forEach((d, i) => {
      const li = document.createElement('li');
      li.textContent = d.file_name;
      li.className = i === activeIndex ? 'active' : '';
      li.dataset.index = String(i);
      list.appendChild(li);
    });
  }

  function close() {
    backdrop.hidden = true;
    input.removeEventListener('input', onInput);
    input.removeEventListener('keydown', onKeydown);
    list.removeEventListener('click', onListClick);
    backdrop.removeEventListener('mousedown', onBackdropClick);
    // Focus returns to whatever had it before Ctrl+K -- usually the Jump
    // button itself, but never assumed: this works regardless of how the
    // palette was opened.
    if (previouslyFocused && typeof previouslyFocused.focus === 'function') previouslyFocused.focus();
  }

  function openSelected() {
    const d = filtered[activeIndex];
    if (!d) return;
    // Checked again here, not only before the palette opened: a Finish can
    // start WHILE the palette is sitting open and the reviewer then presses
    // Enter or clicks an item.
    if (warnFinishing()) return;
    close();
    go(docUrl(d.id));
  }

  function onInput() {
    filtered = filterJump(candidates, input.value);
    activeIndex = 0;
    renderList();
  }
  function onKeydown(e) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      activeIndex = filtered.length ? Math.min(activeIndex + 1, filtered.length - 1) : 0;
      renderList();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      activeIndex = Math.max(activeIndex - 1, 0);
      renderList();
    } else if (e.key === 'Enter') {
      e.preventDefault();
      openSelected();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    }
  }
  function onListClick(e) {
    const li = e.target.closest('li');
    if (!li) return;
    activeIndex = Number(li.dataset.index);
    openSelected();
  }
  // mousedown, not click: fires before the input's own blur, and a click
  // that STARTS outside the palette and is dragged onto it never counts as
  // "outside" either way (the target is read at press time, same place).
  function onBackdropClick(e) {
    if (e.target === backdrop) close();
  }

  input.value = '';
  renderList();
  backdrop.hidden = false;
  input.addEventListener('input', onInput);
  input.addEventListener('keydown', onKeydown);
  list.addEventListener('click', onListClick);
  backdrop.addEventListener('mousedown', onBackdropClick);
  input.focus();
}

// worklist.js's initWorkList() keeps the same array the review bar navigates
// over (one source of truth for ordering -- decision 8); getDocs() is its
// accessor onto that cache.
function worklistDocsCache() {
  const docs = workList ? workList.getDocs() : [];
  pruneRecentlyFinished(docs);
  return docs;
}

// An id leaves `recentlyFinished` once the cache itself confirms the
// server's state change -- gone entirely, or present but no longer
// ready/in_review -- so the guard does not grow forever across a long
// session and a LEGITIMATE re-extract of that same id (a future feature)
// is not permanently blocked from ever being navigated to again.
function pruneRecentlyFinished(docs) {
  for (const id of recentlyFinished) {
    const d = docs.find((x) => x.id === id);
    if (!d || !(d.state === 'ready' || d.state === 'in_review')) recentlyFinished.delete(id);
  }
}

// ===== Undo / Redo ==================================================
function wireUndoRedo() {
  const u = document.getElementById('undo-btn');
  const r = document.getElementById('redo-btn');
  u.addEventListener('click', undo);
  r.addEventListener('click', redo);
  // The buttons say what a click will do ("Undo: delete balloon 5"), so a
  // reviewer never has to guess which of several quick edits goes first.
  const sync = () => {
    u.disabled = !canUndo();
    r.disabled = !canRedo();
    u.title = canUndo() ? `Undo: ${undoLabel()} (Ctrl+Z)` : 'Nothing to undo';
    r.title = canRedo() ? `Redo: ${redoLabel()} (Ctrl+Shift+Z)` : 'Nothing to redo';
  };
  on('history', sync);
  sync();
  // A short-lived toast after an undo, with a Redo link: the button and the
  // keyboard both land here, and a mis-click on a balloon's x is the usual
  // reason to undo. One at a time, so ten Ctrl+Z do not stack ten toasts.
  let closeUndoToast = null;
  on('op', ({ kind }) => {
    if (kind !== 'undo') return;
    if (closeUndoToast) closeUndoToast();
    closeUndoToast = toast({
      kind: 'info', title: `Undid: ${redoLabel()}`, timeout: 2200,
      action: { label: 'Redo', onClick: redo },
    });
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
    // Still unknown whether this instance is campaign-only, so the chrome
    // stays hidden (health-pending) and we ask again.
    setTimeout(pingHealth, 5000);
    return;
  }
  // A campaign-only instance 403s every upload, bulk and export route
  // (plan C1 decision 10); hide their controls rather than show buttons
  // that can only fail ([data-campaign-hide], components.css).
  document.body.classList.toggle('campaign-only', !!data.campaign_only);
  document.body.classList.remove('health-pending');
  pill.classList.remove('warn');
  pill.classList.add('ok');
  const backend = data.ocr_backend_active || data.backend || 'OCR';
  const short = String(backend).replace('Backend','');
  label.textContent = `OCR · ${short}`;
  pill.title = `OCR backend: ${backend}${data.cuda ? ' · CUDA' : ''}`;
}

document.addEventListener('DOMContentLoaded', init);
