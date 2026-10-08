// Staging screen (batch design §2): a draft's rows with badges and an
// Include checkbox, Start batch, Discard, and removing a single row. Reuses
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

function rowHtml(doc) {
  const badges = badgesFor(doc).map((b) => b.text).join(' · ');
  const disabled = doc.unreadable ? 'disabled' : '';
  return `
    <tr data-id="${doc.id}">
      <td><input type="checkbox" class="sv-include" ${doc.included ? 'checked' : ''} ${disabled} /></td>
      <td>${doc.file_name}${doc.rel_path ? ` <span class="muted">${doc.rel_path}</span>` : ''}</td>
      <td>${doc.pages ?? '—'}</td>
      <td>${(doc.size / 1024).toFixed(0)} KB</td>
      <td class="muted">${badges}</td>
      <td><button class="btn ghost sv-remove" data-id="${doc.id}">Remove</button></td>
    </tr>`;
}

export function initStagingView({ getBatch, setIncluded, startBatch, discardBatch,
                                  deleteDocument, toast, onStarted } = {}) {
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
    body.innerHTML = view.documents.map(rowHtml).join('');
    const uploadsEl = document.getElementById('sv-uploads');
    if (uploadsEl) {
      uploadsEl.textContent = view.uploads.map(ignoredSummary).filter(Boolean).join(' · ');
    }
    const counts = document.getElementById('sv-counts');
    if (counts) {
      counts.textContent = `${view.counts.included} included · ${view.counts.excluded} excluded`
        + (view.counts.uploading ? ` · ${view.counts.uploading} still uploading` : '');
    }
    const empty = document.getElementById('sv-empty');
    if (empty) {
      // Carried from B2: an empty archive (every entry ignored, or a ZIP
      // with no PDFs at all) must say so, not just show a bare zero-row
      // table that looks like a loading glitch.
      empty.hidden = view.documents.length > 0;
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
    document.getElementById('sv-start')?.addEventListener('click', async () => {
      try {
        const expectedIds = view.documents.map((d) => d.id);
        const res = await startBatch?.(batchId, expectedIds);
        onStarted?.(res);
      } catch (err) {
        toast?.({ kind: 'error', title: 'Could not start the import', msg: String(err?.message || err) });
        await load(batchId);
      }
    });
    document.getElementById('sv-discard')?.addEventListener('click', async () => {
      if (!confirm('Discard this import? Its staged drawings and uploaded files are removed.')) return;
      try { await discardBatch?.(batchId); } catch { /* best-effort, like main.js's existing discardBatch calls */ }
      onStarted?.({ discarded: true });
    });
  }

  return { load };
}
