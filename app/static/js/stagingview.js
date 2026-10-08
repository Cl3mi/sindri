// Staging screen (batch design §2): a draft's rows with badges and an
// Include checkbox, Start batch, Discard, removing a single row, removing an
// unfinished upload, and adding more files into the same draft. Reuses
// badgesFor (app/static/js/badges.js) so the staging view and the old
// single-drawing confirm screen say the same words for the same row.
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
  return tr;
}

// One row for an upload that has not reached `complete` -- still receiving
// or completing, or stuck answering 507 on every /complete retry (a zip
// bomb, CLAUDE.md §1). Built entirely through the DOM API: no innerHTML at
// all, so there is no template to keep free of server values in the first
// place.
function buildUploadRow(u) {
  const row = document.createElement('div');
  row.className = 'sv-upload-row';
  row.dataset.id = u.id;
  const label = document.createElement('span');
  label.className = 'muted';
  const sizeKnown = typeof u.size === 'number';
  const progress = sizeKnown ? ` — ${u.received ?? 0}/${u.size} bytes` : '';
  label.textContent = `${u.name || 'upload'}${u.rel_path ? ` ${u.rel_path}` : ''} (${u.state})${progress}`;
  row.appendChild(label);
  const btn = document.createElement('button');
  btn.className = 'btn ghost sv-remove-upload';
  btn.dataset.id = u.id;
  btn.textContent = 'Remove upload';
  row.appendChild(btn);
  return row;
}

export function initStagingView({ getBatch, setIncluded, startBatch, discardBatch,
                                  deleteDocument, deleteUpload, importFiles, isImportable,
                                  toast, onStarted } = {}) {
  let batchId = null;
  let view = null;

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

  function render() {
    const body = document.getElementById('sv-body');
    if (!body || !view) return;
    body.innerHTML = '';
    for (const doc of view.documents) body.appendChild(buildRow(doc));

    const pending = document.getElementById('sv-pending');
    if (pending) {
      pending.innerHTML = '';
      for (const u of (view.uploads || []).filter((u) => u.state !== 'complete')) {
        pending.appendChild(buildUploadRow(u));
      }
    }

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

  async function addFiles(e, isFolder) {
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
    try {
      await importFiles?.(files, { batchId });
      await load(batchId);
    } catch (err) {
      toast?.({ kind: 'error', title: 'Could not add files', msg: String(err?.message || err) });
      await load(batchId);
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
      try {
        await deleteDocument?.(id);
        await load(batchId);
      } catch (err) {
        toast?.({ kind: 'error', title: 'Could not remove the drawing', msg: String(err?.message || err) });
      }
    });
    document.getElementById('sv-pending')?.addEventListener('click', async (e) => {
      if (!e.target.classList.contains('sv-remove-upload')) return;
      const id = e.target.dataset.id;
      try {
        await deleteUpload?.(id);
        await load(batchId);
      } catch (err) {
        toast?.({ kind: 'error', title: 'Could not remove the upload', msg: String(err?.message || err) });
      }
    });
    document.getElementById('sv-start')?.addEventListener('click', async () => {
      const blocker = startBlocker(view);
      if (blocker) {
        toast?.({ kind: 'warn', title: 'Cannot start yet', msg: blocker });
        return;
      }
      try {
        const expectedIds = view.documents.map((d) => d.id);
        const res = await startBatch?.(batchId, expectedIds);
        onStarted?.(res);
      } catch (err) {
        if (err?.status === 409) {
          // The draft changed under us (another tab staged or removed a row)
          // between the snapshot this screen showed and the server's check --
          // decision in the B2 state doc. Reloading shows the current truth
          // instead of a Start button stuck on a stale error.
          toast?.({ kind: 'warn', title: 'The import changed',
                    msg: 'The import changed — review it and start again.' });
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
