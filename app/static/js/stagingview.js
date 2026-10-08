// Staging screen (batch design §2): a draft's rows with badges and an
// Include checkbox, Start batch, Discard, removing a single row, removing an
// unfinished upload, cancelling an in-flight add, and adding more files into
// the same draft. Reuses badgesFor (app/static/js/badges.js) so the staging
// view and the old single-drawing confirm screen say the same words for the
// same row.
import { badgesFor } from './badges.js';

// "N file(s) ignored (<reasons>)" from the shape uploads.py returns
// (app/batch/staging.py upload_view: `ignored` the capped list, `ignored_total`
// the true count, `ignored_by_reason` a dict of reason -> count) -- carried
// from B2 (CLAUDE.md B3 note): shown here, never in a log (CLAUDE.md §1).
export function ignoredSummary(upload) {
  const total = upload?.ignored_total || 0;
  if (!total) return '';
  const reasons = Object.keys(upload.ignored_by_reason || {})
    .map((r) => r.replaceAll('_', ' '));
  return `${total} file${total === 1 ? '' : 's'} ignored${reasons.length ? ` (${reasons.join(', ')})` : ''}`;
}

// Why Start cannot run yet, or null when it can -- the client-side mirror of
// what the server itself refuses. An unfinished upload beats everything
// else (nothing in the draft can be trusted to be final yet); then nothing
// included; then the out-of-scope guard, which the UI itself can never
// actually trigger (ticking Include on an out-of-scope row is what STORES
// `allowed_out_of_scope` -- staging.py default_included/set_included), but
// costs nothing to check in case another tab's raw API call left the draft
// in a state this screen never produces on its own.
export function startBlocker(view) {
  if (!view) return 'the import has not loaded yet';
  const uploads = view.uploads || [];
  if (uploads.some((u) => u.state !== 'complete')) {
    return 'an upload is still in progress';
  }
  const docs = view.documents || [];
  if (!docs.some((d) => d.included)) {
    return 'nothing is included yet';
  }
  if (docs.some((d) => d.included && d.out_of_scope && !d.allowed_out_of_scope)) {
    return 'an out-of-scope drawing is included without permission';
  }
  return null;
}

// The carried-from-B2 empty-archive message (CLAUDE.md B3 note), split from
// "still uploading" -- the pending-uploads list (below) already explains
// that case, so this stays silent rather than saying something false about
// an import that has simply not finished yet. Only once every upload is
// complete does zero documents mean something definite: either nothing was
// ever ignored (a true empty archive) or everything was ignored (B2's
// original wording).
export function archiveEmptyMessage(view) {
  if (!view) return '';
  const docs = view.documents || [];
  if (docs.length > 0) return '';
  const uploads = view.uploads || [];
  if (uploads.some((u) => u.state !== 'complete')) return '';
  const ignoredTotal = uploads.reduce((sum, u) => sum + (u.ignored_total || 0), 0);
  if (ignoredTotal === 0) return 'The archive held no PDF files.';
  return 'This import holds no readable PDF — every file was ignored or none was a PDF.';
}

// deleteDocument/deleteUpload (api.js) are BEST-EFFORT: they resolve
// { ok, status }, never throw (same convention as discardBatch). A `try {
// await deleteX(id) } catch` around either call is therefore DEAD CODE --
// the promise never rejects, so a refusal (409: the row is mid-extraction,
// an upload is mid-complete) or a network failure (status 0) both resolved
// silently and the row just sat there with no explanation. Inspecting the
// result is the only way to see either. Returns a toast payload, or null
// when the removal succeeded (nothing to say).
export function removalMessage(res) {
  if (!res || res.ok !== false) return null;
  if (res.status === 409) {
    return { kind: 'warn', title: 'The import changed',
             msg: 'The import changed — review it and try again.' };
  }
  return { kind: 'error', title: 'Could not remove it',
           msg: `Could not remove it${res.status ? ` (status ${res.status})` : ''}.` };
}

// Start's two distinct 409s (app/batch/repo.py: `_draft_rows` vs
// `start_batch`) need different copy: one means the draft's document set no
// longer matches what this screen showed (review it); the other means
// start_batch itself already won the race (nothing to review -- the import
// is simply gone from "draft" already). The server's detail is a FIXED
// sentence (CLAUDE.md §4), so comparing it exactly is reliable; anything
// else defaults to the "changed" copy, same as before this distinction
// existed.
const ALREADY_STARTED_DETAIL = 'this import has already started';

export function startConflictCopy(detail) {
  if (detail === ALREADY_STARTED_DETAIL) {
    return { kind: 'info', title: 'Already started',
             msg: 'This import already started.' };
  }
  return { kind: 'warn', title: 'The import changed',
           msg: 'The import changed — review it and start again.' };
}

// After an Add-files run is aborted mid-flight, a file that had already
// reached the server (POST /api/uploads succeeded) leaves an incomplete
// upload row behind -- but importFiles' onFile callback only ever reports
// { sent, total } or { done, result }, never the upload id a cancelled file
// was given, so there is nothing to delete until the NEXT getBatch() shows
// it. Matched the same way importone.js's inspectDraft recognises its own
// upload: by name and size (the only identity importFiles exposes to a
// caller that never sees the id). Only `cancelledFiles` -- files whose
// result was an abort, not a server refusal or a success -- are matched, so
// a file that simply failed for its own reason is left for the user to see
// and act on via the ordinary Remove-upload control.
export function orphanedUploadIds(uploads, cancelledFiles) {
  const incomplete = (uploads || []).filter((u) => u.state !== 'complete');
  const ids = [];
  for (const f of cancelledFiles || []) {
    const name = String(f?.name || '').normalize('NFC').trim();
    const match = incomplete.find((u) => u.name === name && u.size === f.size);
    if (match) ids.push(match.id);
  }
  return ids;
}

// The merged list #sv-pending renders: uploads the SERVER already knows
// about (still receiving/completing, or stuck on a repeating 507 -- CLAUDE.md
// §1) plus files an Add-files run in THIS tab is still sending, which the
// server does not know about between POST /api/uploads and the next reload.
// Local entries never duplicate a server one: a file only ever appears
// locally until load() picks up the real row, at which point `done` (set by
// importFiles' onFile) has already dropped it from `addProgress`.
export function pendingItems(uploads, addProgress) {
  const serverRows = (uploads || []).filter((u) => u.state !== 'complete')
    .map((u) => ({ id: u.id, name: u.name, rel_path: u.rel_path, state: u.state,
                   received: u.received, size: u.size, local: false }));
  const localRows = (addProgress || []).filter((p) => !p.done)
    .map((p, i) => ({ id: `local-${i}`, name: p.name, rel_path: '', state: 'uploading',
                      received: p.sent, size: p.total, local: true }));
  return [...serverRows, ...localRows];
}

// Every value below that comes from the server (file name, rel path, a
// duplicate's file/batch name, badge text, an upload's name) is set via
// textContent or a DOM text node, never interpolated into innerHTML -- a
// client's own file or folder name must never be able to inject markup.
// `tr.innerHTML` here is a FIXED template with no server value in it (same
// convention as worklist.js's row()); only `checked`/`disabled`, which are
// derived booleans, not server strings, go into the template itself.
function buildRow(doc) {
  const tr = document.createElement('tr');
  tr.dataset.id = doc.id;
  const checked = doc.included ? 'checked' : '';
  const disabled = doc.unreadable ? 'disabled' : '';
  tr.innerHTML = `
    <td><input type="checkbox" class="sv-include" ${checked} ${disabled} /></td>
    <td class="sv-file"></td>
    <td class="sv-pages"></td>
    <td class="sv-size"></td>
    <td class="muted sv-badges"></td>
    <td><button class="btn ghost sv-remove">Remove</button></td>`;
  const fileCell = tr.querySelector('.sv-file');
  fileCell.appendChild(document.createTextNode(doc.file_name));
  if (doc.rel_path) {
    fileCell.appendChild(document.createTextNode(' '));
    const rel = document.createElement('span');
    rel.className = 'muted';
    rel.textContent = doc.rel_path;
    fileCell.appendChild(rel);
  }
  tr.querySelector('.sv-pages').textContent = doc.pages ?? '—';
  tr.querySelector('.sv-size').textContent = `${(doc.size / 1024).toFixed(0)} KB`;
  tr.querySelector('.sv-badges').textContent = badgesFor(doc).map((b) => b.text).join(' · ');
  tr.querySelector('.sv-remove').dataset.id = doc.id;
  // setAttribute never parses its value as markup (unlike innerHTML), so a
  // file name with quotes or angle brackets in it is still safe here (same
  // note as worklist.js's row()).
  tr.querySelector('.sv-include')?.setAttribute('aria-label', `Include ${doc.file_name}`);
  return tr;
}

// One row in #sv-pending: a server-known incomplete upload (Remove upload,
// DELETE /api/uploads/{id}) or a local in-flight one from an Add-files run
// still happening in this tab (Cancel, which aborts the whole run's shared
// AbortController -- importFiles offers one signal per call, not per file,
// so that is the most precise cancel available without changing upload.js).
// Built entirely through the DOM API: no innerHTML at all, so there is no
// template to keep free of server values in the first place.
function buildPendingRow(item) {
  const row = document.createElement('div');
  row.className = 'sv-upload-row';
  row.dataset.id = item.id;
  const label = document.createElement('span');
  label.className = 'muted';
  const sizeKnown = typeof item.size === 'number';
  const progress = sizeKnown ? ` — ${item.received ?? 0}/${item.size} bytes` : '';
  label.textContent = `${item.name || 'upload'}${item.rel_path ? ` ${item.rel_path}` : ''} (${item.state})${progress}`;
  row.appendChild(label);
  const btn = document.createElement('button');
  if (item.local) {
    btn.className = 'btn ghost sv-cancel-add';
    btn.textContent = 'Cancel';
  } else {
    btn.className = 'btn ghost sv-remove-upload';
    btn.dataset.id = item.id;
    btn.textContent = 'Remove upload';
  }
  row.appendChild(btn);
  return row;
}

export function initStagingView({ getBatch, setIncluded, startBatch, discardBatch,
                                  deleteDocument, deleteUpload, importFiles, isImportable,
                                  toast, onStarted } = {}) {
  let batchId = null;
  let view = null;
  // An Add-files/Add-folder run in progress: the shared controller Cancel
  // aborts, and each file's live progress until importFiles marks it done
  // (success, failure or cancellation) and load() replaces it with the
  // server's own row.
  let addController = null;
  let addProgress = [];

  async function load(id) {
    batchId = id;
    if (!getBatch) return;
    try {
      view = await getBatch(id);
      render();
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not load the import', msg: String(err?.message || err) });
    }
  }

  function renderPending() {
    const pending = document.getElementById('sv-pending');
    if (!pending) return;
    pending.innerHTML = '';
    for (const item of pendingItems(view?.uploads, addProgress)) {
      pending.appendChild(buildPendingRow(item));
    }
  }

  function render() {
    const body = document.getElementById('sv-body');
    if (!body || !view) return;
    body.innerHTML = '';
    for (const doc of view.documents) body.appendChild(buildRow(doc));

    renderPending();

    const uploadsEl = document.getElementById('sv-uploads');
    if (uploadsEl) {
      uploadsEl.textContent = view.uploads.map(ignoredSummary).filter(Boolean).join(' · ');
    }
    const counts = document.getElementById('sv-counts');
    if (counts) {
      counts.textContent = `${view.counts.included} included · ${view.counts.excluded} excluded`
        + (view.counts.uploading ? ` · ${view.counts.uploading} still uploading` : '');
    }
    const blocker = startBlocker(view);
    const startBtn = document.getElementById('sv-start');
    if (startBtn) startBtn.disabled = Boolean(blocker);
    const reasonEl = document.getElementById('sv-start-reason');
    if (reasonEl) reasonEl.textContent = blocker || '';

    const empty = document.getElementById('sv-empty');
    if (empty) {
      const msg = archiveEmptyMessage(view);
      empty.textContent = msg;
      empty.hidden = !msg;
    }
  }

  function setAddButtonsDisabled(disabled) {
    const files = document.getElementById('sv-add-files');
    const folder = document.getElementById('sv-add-folder');
    if (files) files.disabled = disabled;
    if (folder) folder.disabled = disabled;
  }

  async function addFiles(e, isFolder) {
    if (addController) return;   // a run is already active; the buttons are disabled too
    const picked = [...(e.target.files || [])];
    e.target.value = '';   // a picker holding the same files fires no 'change' on a re-pick otherwise
    if (!picked.length) return;
    // The plain-files picker's `accept` already keeps non-PDF/ZIP files out
    // of the dialog; a folder picker has no such filter (main.js's
    // wireImport carries the same note), so its contents are screened the
    // same way the server would judge them.
    const files = isFolder ? picked.filter((f) => isImportable?.(f.name)) : picked;
    if (!files.length) {
      toast?.({ kind: 'warn', title: 'Nothing to import',
                msg: 'No .pdf or .zip files were found in that folder.' });
      return;
    }
    addController = new AbortController();
    addProgress = files.map((f) => ({ name: f.name, sent: 0, total: f.size, done: false }));
    setAddButtonsDisabled(true);
    renderPending();   // show the run starting at 0 bytes, before the first server round trip
    let results = [];
    try {
      results = (await importFiles?.(files, {
        batchId,
        signal: addController.signal,
        onFile: (i, info) => {
          if (!addProgress[i]) return;
          if (info.done) {
            addProgress[i] = { ...addProgress[i], done: true };
          } else {
            addProgress[i] = { ...addProgress[i], sent: info.sent, total: info.total };
          }
          renderPending();
        },
      })) || [];
    } finally {
      // Files that were aborted (not refused, not simply failed) may have
      // left an incomplete upload on the server that importFiles never
      // handed back an id for -- orphanedUploadIds matches it by name/size
      // after the reload below picks it up, so Cancel does not just stop
      // the network traffic but also tidies up what it already sent.
      const cancelledFiles = files.filter((f, i) =>
        results[i] && results[i].ok === false && results[i].error?.message === 'upload cancelled');
      addController = null;
      addProgress = [];
      setAddButtonsDisabled(false);
      await load(batchId);
      if (cancelledFiles.length && view) {
        const ids = orphanedUploadIds(view.uploads, cancelledFiles);
        for (const id of ids) await deleteUpload?.(id);
        if (ids.length) await load(batchId);
      }
    }
    const failed = results.filter((r) => r && r.ok === false && r.error?.message !== 'upload cancelled').length;
    if (failed) {
      toast?.({ kind: 'warn', title: 'Some files did not import',
                msg: `${failed} of ${files.length} failed.` });
    }
  }

  // Deviation from the plan's literal text: these `document.getElementById`
  // calls ran at the TOP of initStagingView, unguarded. In the node-test
  // environment the stagingview.test.mjs import-smoke test runs in, there is
  // no DOM at all -- not even a null `document`, an undefined GLOBAL -- so
  // `document.getElementById(...)` throws ReferenceError before
  // initStagingView ever returns, and the plan's own
  // `assert.doesNotThrow(() => mod.initStagingView({}))` would fail. Guarding
  // the whole wiring block keeps real-browser behaviour identical (the block
  // still runs, in order, the first time this module is loaded there) while
  // making the function callable with no DOM present, same as every other
  // screen module's import-smoke test relies on implicitly.
  if (typeof document !== 'undefined') {
    document.getElementById('sv-body')?.addEventListener('change', async (e) => {
      if (!e.target.classList.contains('sv-include')) return;
      const id = e.target.closest('tr')?.dataset.id;
      if (!id) return;
      try {
        await setIncluded?.(id, e.target.checked);
        await load(batchId);
      } catch (err) {
        toast?.({ kind: 'error', title: 'Could not change the selection', msg: String(err?.message || err) });
        await load(batchId);
      }
    });
    document.getElementById('sv-body')?.addEventListener('click', async (e) => {
      if (!e.target.classList.contains('sv-remove')) return;
      const id = e.target.dataset.id;
      // deleteDocument is best-effort (api.js): it resolves { ok, status },
      // never throws, so the result -- not a catch -- is what says whether
      // the removal actually happened.
      const res = await deleteDocument?.(id);
      const msg = removalMessage(res);
      if (msg) toast?.(msg);
      await load(batchId);
    });
    document.getElementById('sv-pending')?.addEventListener('click', async (e) => {
      if (e.target.classList.contains('sv-cancel-add')) {
        addController?.abort();
        return;
      }
      if (!e.target.classList.contains('sv-remove-upload')) return;
      const id = e.target.dataset.id;
      // deleteUpload is best-effort too (api.js) -- same fix as above.
      const res = await deleteUpload?.(id);
      const msg = removalMessage(res);
      if (msg) toast?.(msg);
      await load(batchId);
    });
    document.getElementById('sv-start')?.addEventListener('click', async () => {
      const blocker = startBlocker(view);
      if (blocker) {
        toast?.({ kind: 'warn', title: 'Cannot start yet', msg: blocker });
        return;
      }
      const startBtn = document.getElementById('sv-start');
      // Disabled SYNCHRONOUSLY, before the first await: a second click
      // landing while the first request is still in flight must never be
      // able to fire a second Start (the server's own `expected_ids` guard
      // closes the cross-tab version of this race, not the same-tab
      // double-click one).
      if (startBtn) startBtn.disabled = true;
      try {
        const expectedIds = view.documents.map((d) => d.id);
        const res = await startBatch?.(batchId, expectedIds);
        onStarted?.(res);
      } catch (err) {
        // Re-enabled only here: on success the screen is about to navigate
        // away (onStarted), so there is nothing left to re-enable for.
        if (startBtn) startBtn.disabled = false;
        if (err?.status === 409) {
          const copy = startConflictCopy(err?.message);
          toast?.(copy);
        } else {
          toast?.({ kind: 'error', title: 'Could not start the import', msg: String(err?.message || err) });
        }
        await load(batchId);
      }
    });
    document.getElementById('sv-discard')?.addEventListener('click', async () => {
      if (!confirm('Discard this import? Its staged drawings and uploaded files are removed.')) return;
      try { await discardBatch?.(batchId); } catch { /* best-effort, like main.js's existing discardBatch calls */ }
      onStarted?.({ discarded: true });
    });
    document.getElementById('sv-add-files')?.addEventListener('click', () => {
      document.getElementById('sv-add-files-input')?.click();
    });
    document.getElementById('sv-add-files-input')?.addEventListener('change', (e) => addFiles(e, false));
    document.getElementById('sv-add-folder')?.addEventListener('click', () => {
      document.getElementById('sv-add-folder-input')?.click();
    });
    document.getElementById('sv-add-folder-input')?.addEventListener('change', (e) => addFiles(e, true));
  }

  return { load };
}
